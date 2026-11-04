from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_release_source_binding_behavior import (
    BUILDER, SOURCE, source_proof, source_run, source_workflow,
)

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/gh-read-retry.py"
VERIFIER = ROOT / "scripts/verify-source-attestation.sh"
SPEC = importlib.util.spec_from_file_location("release_read_retry", HELPER)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
p=pathlib.Path(os.environ['READ_TEST_STATE']); a=sys.argv[1:]
if a[0]=='api': key='run' if '/actions/runs/' in a[1] else 'workflow'
elif a[:2]==['run','download']: key='download'
elif a[:2]==['attestation','verify']: key='attestation'
elif a[:2]==['secret','list']: key='secrets'
else: raise SystemExit(99)
calls=json.loads((p/'calls').read_text()) if (p/'calls').exists() else []
calls.append({'key':key,'argv':a}); (p/'calls').write_text(json.dumps(calls))
n=sum(x['key']==key for x in calls)
if key==os.environ.get('FAIL_KEY') and n<=int(os.environ.get('FAIL_COUNT','0')):
 if key=='download':
  dest=pathlib.Path(a[a.index('--dir')+1]); (dest/'stale-partial').write_text('partial')
 sys.stdout.write('partial untrusted response')
 sys.stderr.write(os.environ.get('FAIL_ERROR','Get "https://api.github.com/read": EOF\n'))
 raise SystemExit(1)
if key=='download':
 dest=pathlib.Path(a[a.index('--dir')+1])
 (dest/'source-attestation.json').write_text((p/'proof').read_text())
elif key in ('run','workflow'): sys.stdout.write((p/key).read_text())
elif key=='secrets':
 print('\n'.join(['SRC_DEPLOY_KEY','R2_KEY_ID','R2_APP_KEY','CLOUDFLARE_R2_CONFIG_TOKEN',
                  'KEYSTORE_BASE64','KEYSTORE_PASSWORD','KEY_ALIAS','KEY_PASSWORD']))
'''


class ReleaseReadRetryTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.path = Path(self.owner.name)
        self.bin = self.path / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        (self.path / "run").write_text(source_run())
        (self.path / "workflow").write_text(source_workflow())
        (self.path / "proof").write_text(source_proof())
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "READ_TEST_STATE": str(self.path)}

    def calls(self, key=None):
        p = self.path / "calls"
        values = json.loads(p.read_text()) if p.exists() else []
        return [v for v in values if key is None or v["key"] == key]

    def verifier(self, key="", failures=0, error=None):
        env = {**self.env, "FAIL_KEY": key, "FAIL_COUNT": str(failures)}
        if error is not None:
            env["FAIL_ERROR"] = error
        return subprocess.run(["/bin/bash", str(VERIFIER), "v9.8.7", SOURCE, BUILDER, "12345"],
                              env=env, capture_output=True, text=True, timeout=20)

    def test_actual_verifier_retries_transport_and_discards_partial_stdout(self):
        result = self.verifier("run", 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("run")), 2)
        self.assertEqual(len(self.calls("attestation")), 1)
        self.assertNotIn("partial untrusted", result.stdout)

    def test_actual_verifier_download_retry_uses_fresh_directory(self):
        result = self.verifier("download", 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls("download")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["argv"][-1], calls[1]["argv"][-1])
        self.assertTrue(all(not Path(c["argv"][-1]).exists() for c in calls))
        self.assertEqual(len(self.calls("attestation")), 1)

    def test_actual_provenance_transport_retry_still_reaches_verifier(self):
        result = self.verifier("attestation", 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("attestation")), 2)

    def test_rejected_signature_with_network_suffix_is_not_replayed(self):
        result = self.verifier("attestation", 1,
                               'invalid signature\nGet "https://api.github.com/read": EOF\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("attestation")), 1)
        self.assertNotIn("partial untrusted", result.stdout)

    def test_permission_rejection_is_not_replayed(self):
        result = self.verifier("run", 1, 'HTTP 403\nGet "https://api.github.com/read": EOF\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_unknown_failure_before_transport_line_is_not_replayed(self):
        result = self.verifier("attestation", 1,
                               'unknown proof failure\nGet "https://api.github.com/read": EOF\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("attestation")), 1)

    def native_gh_tls_failure(self, certificate_rejection=False):
        """A loopback proxy closes a real gh TLS handshake; no external I/O."""
        context = None
        if certificate_rejection:
            key, cert = self.path / "key.pem", self.path / "cert.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", str(key), "-out", str(cert), "-days", "1",
                            "-subj", "/CN=api.github.com", "-addext", "subjectAltName=DNS:api.github.com"],
                           check=True, capture_output=True, timeout=10)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)

        class Proxy(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(5)
                request = b""
                while b"\r\n\r\n" not in request and len(request) < 16384:
                    part = self.request.recv(4096)
                    if not part:
                        return
                    request += part
                self.server.receipts.append(request.split(b"\r\n", 1)[0])
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if context is None:
                    # ClientHello proves the native gh request reached TLS.
                    self.server.hellos.append(self.request.recv(4096))
                else:
                    try:
                        with context.wrap_socket(self.request, server_side=True):
                            self.server.accepted = True
                    except ssl.SSLError:
                        self.server.rejected += 1

        env = {k: v for k, v in os.environ.items() if k.upper() not in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "GH_HOST", "GH_TOKEN",
            "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG",
        }}
        # Use the actual installed gh, not this suite's fake gh fixture.
        self.assertIsNotNone(shutil.which("gh", path=env["PATH"]))
        config = self.path / "gh-config"
        config.mkdir()
        env.update(GH_TOKEN="loopback-test-token", GH_CONFIG_DIR=str(config), NO_PROXY="")
        with socketserver.TCPServer(("127.0.0.1", 0), Proxy) as server:
            server.receipts, server.hellos, server.rejected, server.accepted = [], [], 0, False
            env["HTTPS_PROXY"] = f"http://127.0.0.1:{server.server_address[1]}"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([sys.executable, str(HELPER), "gh", "api",
                                         "repos/onesyue/yuelink-ci/commits/" + BUILDER],
                                        env=env, capture_output=True, text=True, timeout=15)
            finally:
                server.shutdown()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            expected = 1 if certificate_rejection else 3
            self.assertEqual(server.receipts, [b"CONNECT api.github.com:443 HTTP/1.1"] * expected)
            if certificate_rejection:
                self.assertIn("certificate", result.stderr.lower())
                self.assertEqual(server.rejected, 1)
                self.assertFalse(server.accepted)
            else:
                self.assertIn("EOF", result.stderr)
                self.assertEqual(len(server.hellos), 3)
                self.assertTrue(all(h.startswith(b"\x16\x03") for h in server.hellos))

    def test_actual_gh_eof_is_bounded_and_never_treated_as_success(self):
        self.native_gh_tls_failure()

    def test_actual_gh_certificate_rejection_is_not_retried(self):
        self.native_gh_tls_failure(certificate_rejection=True)

    def test_successful_bad_content_is_rejected_by_original_consumers(self):
        for name, bad in [("run", source_run(head_sha="f" * 40)),
                          ("proof", source_proof(gates=[]))]:
            with self.subTest(name=name):
                (self.path / name).write_text(bad)
                result = self.verifier()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(self.calls("attestation")), 0)
                (self.path / "calls").unlink()
                (self.path / "run").write_text(source_run())
                (self.path / "proof").write_text(source_proof())

    def test_exhausted_transport_fails_after_three_attempts(self):
        result = self.verifier("run", 10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 3)
        self.assertNotIn("partial untrusted", result.stdout)

    def test_missing_installed_helper_fails_before_gh(self):
        missing = self.path / "verify-source-attestation.sh"
        missing.write_bytes(VERIFIER.read_bytes())
        result = subprocess.run(["bash", str(missing), "v9.8.7", SOURCE, BUILDER, "12345"],
                                env=self.env, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_write_and_weakened_signature_commands_never_execute(self):
        for argv in [["gh", "api", "repos/onesyue/yuelink-ci/git/refs", "-X", "POST"],
                     ["gh", "workflow", "run", "build.yml"],
                     ["git", "push", "origin", "v9.8.7"],
                     ["gh", "attestation", "verify", "x", "--custom-trusted-root", "y"]]:
            with self.subTest(argv=argv):
                result = subprocess.run([sys.executable, str(HELPER), *argv], env=self.env,
                                        capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_attempt_deadline_kills_actual_descendant_and_never_returns_partial(self):
        pidfile = self.path / "child-pid"
        program = ("import subprocess,sys,time,pathlib; "
                   "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
                   "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); "
                   "print('partial',flush=True);time.sleep(60)")
        code, stdout, _ = m.execute([sys.executable, "-c", program, str(pidfile)], 0.5)
        self.assertEqual(code, 124)
        self.assertEqual(stdout, b"")
        self.assertTrue(pidfile.exists())  # prove the descendant actually ran
        child = subprocess.run(["ps", "-p", pidfile.read_text(), "-o", "stat="],
                               capture_output=True, text=True)
        self.assertTrue(child.returncode != 0 or child.stdout.strip().startswith("Z"), child.stdout)

    def test_total_deadline_prevents_another_attempt(self):
        argv = ["gh", "api", "repos/onesyue/yuelink-ci/commits/" + BUILDER]
        with patch.object(m, "TOTAL_SECONDS", 1), patch.object(m, "execute", return_value=(
                1, b"partial", b'Get "https://api.github.com/read": EOF\n')) as execute:
            self.assertEqual(m.run(argv), 124)
            self.assertEqual(execute.call_count, 1)

    def test_actual_release_entry_reaches_secret_read_retry_and_keeps_bad_tag_red(self):
        git = self.bin / "git"
        git.write_text('#!/bin/sh\ncase "$*" in\n"branch --show-current") echo master;;\n'
                       '"status --porcelain"|"fetch --prune --no-tags origin master") :;;\n'
                       '"rev-parse HEAD"|"rev-parse origin/master") printf "%040d\\n" 1;;\n'
                       '*) exit 98;;\nesac\n')
        git.chmod(0o755)
        result = subprocess.run(["bash", str(ROOT / "release.sh"), "v9.8.7"],
                                env={**self.env, "FAIL_KEY": "secrets", "FAIL_COUNT": "1"},
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)  # fake tag body is deliberately invalid
        self.assertEqual(len(self.calls("secrets")), 2)
        self.assertGreater(len(self.calls()), 2)  # reached the private tag consumer
        self.assertNotIn("partial untrusted", result.stdout)


if __name__ == "__main__":
    unittest.main()
