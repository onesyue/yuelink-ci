"""Ratchet for tunable, host-path, domain and port literals in CI scripts.

The classes match the yueto-ci literal lint: numeric literals on timeout,
interval, ttl, retry, delay, limit, threshold, budget, batch, worker, max
and min names; numeric literals passed to sleep; absolute host paths;
http(s) URLs and yue.to / yuetong / yuetoto domains; and port literals.
Shell files use the same classes for assignments, sleep, connect timeouts,
URLs and host paths.

``scripts/constants.py`` is the config module. ``scripts/brand_literals.py``
is the brand projection. Counts in ``policy/literal-classes.json`` may only
go down.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "policy" / "literal-classes.json"
CONSTANTS_PATH = ROOT / "scripts" / "constants.py"
CLASSES = ("tunable", "host-path", "domain", "port")
SKIPPED = {
    "scripts/brand_literals.py": "brand projection; domains are defined there",
    "scripts/constants.py": "config module; values are defined there",
}
TUNABLE_NAME = re.compile(
    r"timeout|interval|ttl|retries|retry|delay|threshold|budget|batch|workers|limit|max|min",
    re.I,
)
SHELL_NAME = re.compile(
    r"timeout|interval|ttl|retries|retry|delay|threshold|budget|batch|workers|limit|max|min|seconds|attempts|lifetime",
    re.I,
)
PORT_NAME = re.compile(r"(?:^|_)port(?:$|_)", re.I)
HOST_PREFIXES = ("/etc/", "/var/lib/", "/opt/", "/run/", "/usr/local/")
URL_RE = re.compile(r"https?://[A-Za-z0-9]")
BRAND_RE = re.compile(
    r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.)*(?:yue\.to|yuetong\.[A-Za-z0-9.-]+|yuetoto\.[A-Za-z0-9.-]+)",
    re.I,
)
PORT_TEXT_RE = re.compile(
    r"(?:(?:\d{1,3}\.){3}\d{1,3}|localhost|postgres):(\d{2,5})(?=/|:|$|\?|\s)"
    r"|:(\d{2,5})/"
)
SHELL_ASSIGN = re.compile(
    r"(?:^|[^\w])([A-Za-z_][A-Za-z0-9_]*)=\"?(-?[0-9]+(?:\.[0-9]+)?)\"?(?:\s|$)"
)
SHELL_SLEEP = re.compile(r"(?:^|[\s;|&])sleep\s+([0-9]+(?:\.[0-9]+)?)\b")
SHELL_CONNECT = re.compile(r"--connect-timeout\s+([0-9]+(?:\.[0-9]+)?)\b")
SKIP_NUMBERS = {0, 1, -1}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _number(node: ast.expr) -> int | float | None:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        if node.value in SKIP_NUMBERS:
            return None
        return node.value
    return None


def _host_path(text: str) -> bool:
    for prefix in HOST_PREFIXES:
        start = 0
        while True:
            found = text.find(prefix, start)
            if found < 0:
                break
            if found == 0 or not (text[found - 1].isalnum() or text[found - 1] in "._-"):
                return True
            start = found + len(prefix)
    return False


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits = {name: 0 for name in CLASSES}
        self.notes: list[str] = []

    def _add(self, kind: str, line: int, detail: object) -> None:
        self.hits[kind] += 1
        self.notes.append(f"L{line} {kind} {detail!r}"[:180])

    def _tunable(self, node: ast.expr, line: int, detail: str) -> None:
        if _number(node) is not None:
            self._add("tunable", line, detail)

    def _port_number(self, node: ast.expr, line: int, detail: str) -> None:
        if _number(node) is not None:
            self._add("port", line, detail)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            if _host_path(node.value):
                self._add("host-path", node.lineno, node.value[:80])
            if URL_RE.search(node.value) or BRAND_RE.search(node.value):
                self._add("domain", node.lineno, node.value[:80])
            if PORT_TEXT_RE.search(node.value):
                self._add("port", node.lineno, node.value[:80])
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg and TUNABLE_NAME.search(node.arg):
            self._tunable(node.value, node.lineno, node.arg)
        if node.arg and PORT_NAME.search(node.arg):
            self._port_number(node.value, node.lineno, node.arg)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if TUNABLE_NAME.search(name):
                self._tunable(node.value, node.lineno, name)
            if PORT_NAME.search(name):
                self._port_number(node.value, node.lineno, name)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            name = node.target.id
            if TUNABLE_NAME.search(name):
                self._tunable(node.value, node.lineno, name)
            if PORT_NAME.search(name):
                self._port_number(node.value, node.lineno, name)
        self.generic_visit(node)

    def _defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        positional = node.args.posonlyargs + node.args.args
        for arg, default in zip(positional[-len(node.args.defaults):], node.args.defaults):
            if TUNABLE_NAME.search(arg.arg):
                self._tunable(default, default.lineno, arg.arg)
            if PORT_NAME.search(arg.arg):
                self._port_number(default, default.lineno, arg.arg)
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            if default is None:
                continue
            if TUNABLE_NAME.search(arg.arg):
                self._tunable(default, default.lineno, arg.arg)
            if PORT_NAME.search(arg.arg):
                self._port_number(default, default.lineno, arg.arg)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._defaults(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _call_name(node.func) == "sleep":
            for arg in node.args:
                self._tunable(arg, getattr(arg, "lineno", node.lineno), "sleep")
        self.generic_visit(node)


def _shell_hits(path: Path) -> tuple[dict[str, int], list[str]]:
    hits = {name: 0 for name in CLASSES}
    notes: list[str] = []

    def add(kind: str, line: int, detail: object) -> None:
        hits[kind] += 1
        notes.append(f"L{line} {kind} {detail!r}"[:180])

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _host_path(line):
            add("host-path", line_number, stripped[:80])
        if URL_RE.search(line) or BRAND_RE.search(line):
            add("domain", line_number, stripped[:80])
        if PORT_TEXT_RE.search(line):
            add("port", line_number, stripped[:80])
        sleep = SHELL_SLEEP.search(line)
        if sleep is not None and float(sleep.group(1)) not in SKIP_NUMBERS:
            add("tunable", line_number, f"sleep {sleep.group(1)}")
        connect = SHELL_CONNECT.search(line)
        if connect is not None and float(connect.group(1)) not in SKIP_NUMBERS:
            add("tunable", line_number, f"connect-timeout {connect.group(1)}")
        for match in SHELL_ASSIGN.finditer(line):
            raw = float(match.group(2)) if "." in match.group(2) else int(match.group(2))
            if raw in SKIP_NUMBERS or not SHELL_NAME.search(match.group(1)):
                continue
            add("tunable", line_number, f"{match.group(1)}={match.group(2)}")
    return hits, notes


def scan() -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = {}
    notes: list[str] = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in SKIPPED:
            continue
        scanner = _Scanner()
        scanner.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        positive = {kind: count for kind, count in scanner.hits.items() if count}
        if positive:
            found[relative] = positive
            notes.extend(f"{relative} {note}" for note in scanner.notes)
    shell_paths = [ROOT / "release.sh", *sorted((ROOT / "scripts").glob("*.sh"))]
    for path in shell_paths:
        relative = path.relative_to(ROOT).as_posix()
        hits, shell_notes = _shell_hits(path)
        positive = {kind: count for kind, count in hits.items() if count}
        if positive:
            found[relative] = positive
            notes.extend(f"{relative} {note}" for note in shell_notes)
    scan.notes = notes  # type: ignore[attr-defined]
    return found


def _positive(files: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    cleaned: dict[str, dict[str, int]] = {}
    for path, counts in files.items():
        kept = {kind: int(counts.get(kind, 0)) for kind in CLASSES if int(counts.get(kind, 0))}
        if kept:
            cleaned[path] = kept
    return cleaned


def _head_baseline() -> dict[str, dict[str, int]] | None:
    result = subprocess.run(
        ["git", "show", "HEAD:policy/literal-classes.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout)
    return _positive(payload.get("files", {}))


class LiteralClassRatchetTest(unittest.TestCase):
    def test_current_counts_match_baseline(self) -> None:
        current = scan()
        payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(list(payload["classes"]), list(CLASSES))
        self.assertEqual(payload["area"], "scripts")
        baseline = _positive(payload["files"])
        if current != baseline:
            self.fail(
                "literal counts drifted\ncurrent "
                + json.dumps(current, sort_keys=True)
                + "\nbaseline "
                + json.dumps(baseline, sort_keys=True)
                + "\n"
                + "\n".join(getattr(scan, "notes", []))
            )

    def test_baseline_counts_only_go_down(self) -> None:
        previous = _head_baseline()
        if previous is None:
            return
        payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        current = _positive(payload["files"])
        for path in set(previous) | set(current):
            for kind in CLASSES:
                before = previous.get(path, {}).get(kind, 0)
                after = current.get(path, {}).get(kind, 0)
                self.assertLessEqual(
                    after,
                    before,
                    f"{path} {kind} rose from {before} to {after}",
                )

    def test_constants_keep_the_previous_values(self) -> None:
        constants = _load(CONSTANTS_PATH, "ci_constants")
        expected = {
            "YUELINK_PRIVATE_REPOSITORY": "https://github.com/onesyue/yuelink.git",
            "ACTIONS_OIDC_ISSUER": "https://token.actions.githubusercontent.com",
            "ANTIFREEZE_GIT_TIMEOUT_SECONDS": 30,
            "IOS_COMPILER_DISCOVERY_TIMEOUT_SECONDS": 60,
            "IOS_NATIVE_BUILD_TIMEOUT_SECONDS": 2400,
            "IOS_GIT_REV_PARSE_TIMEOUT_SECONDS": 60,
            "IOS_SIMCTL_LIST_TIMEOUT_SECONDS": 120,
            "IOS_OTOOL_PROBE_TIMEOUT_SECONDS": 120,
            "GH_COMMAND_TIMEOUT_SECONDS": 120,
            "CI_CHECK_ATTEMPTS": 30,
            "CI_CHECK_INTERVAL_SECONDS": 20,
            "GH_PROCESS_LIST_TIMEOUT_SECONDS": 2,
            "GH_PROCESS_GROUP_POLL_SECONDS": 0.05,
            "GH_READ_ATTEMPTS": 3,
            "GH_READ_ATTEMPT_SECONDS": 60,
            "GH_READ_TOTAL_SECONDS": 150,
            "AUTH_STORAGE_COMMAND_TIMEOUT_SECONDS": 1200,
            "ATTESTATION_DOWNLOAD_TIMEOUT_SECONDS": 150,
            "CHANNEL_FETCH_TIMEOUT_SECONDS": 30,
            "COVERAGE_WORKER_TIMEOUT_SECONDS": 350,
            "COVERAGE_VM_STAT_TIMEOUT_SECONDS": 5,
            "COVERAGE_GITLINK_TIMEOUT_SECONDS": 120,
            "COVERAGE_FLUTTER_VERSION_TIMEOUT_SECONDS": 30,
            "COVERAGE_GROUP_KILL_AFTER_SECONDS": 30,
            "COVERAGE_GROUP_DEADLINE_SECONDS": 300,
            "COVERAGE_PUB_GET_TIMEOUT_SECONDS": 120,
            "COVERAGE_WORKTREE_REMOVE_TIMEOUT_SECONDS": 60,
            "GO_MOD_TIDY_TIMEOUT_SECONDS": 120,
            "GO_MOD_EDIT_TIMEOUT_SECONDS": 10,
            "GO_LIST_TIMEOUT_SECONDS": 30,
            "OSV_SCAN_TIMEOUT_SECONDS": 105,
            "ARTIFACT_LIST_TIMEOUT_SECONDS": 60,
            "RELEASE_CLOSURE_FETCH_TIMEOUT_SECONDS": 120,
            "CURL_CONNECT_TIMEOUT_SECONDS": 10,
            "SOURCE_FETCH_LOW_SPEED_BYTES_PER_SECOND": 1000,
            "SOURCE_FETCH_LOW_SPEED_SECONDS": 60,
            "SOURCE_FETCH_ATTEMPTS": 3,
            "PUBLIC_TAG_LOOKUP_ATTEMPTS": 3,
            "PUBLIC_TAG_VERIFY_ATTEMPTS": 12,
            "PUBLIC_TAG_VERIFY_INTERVAL_SECONDS": 5,
            "R2_PROOF_LIFETIME_SECONDS": 1800,
            "MAX_MANIFEST_LIFETIME_SECONDS": 45 * 24 * 60 * 60,
            "MAX_PUBLISH_CLOCK_SKEW_SECONDS": 6 * 60 * 60,
            "UNTAGGED_SHIPPED_SOURCE_SECONDS": 24 * 60 * 60,
            "R2_LOCK_FETCH_BUDGET_SECONDS": 30,
            "R2_LOCK_FETCH_ATTEMPTS": 3,
            "R2_LOCK_FETCH_ATTEMPT_CAP_SECONDS": 10,
            "MIN_TEST_FILES": 8,
            "MIN_HASHED_FILES": 20,
            "MIN_FILES": 80,
            "MIN_PRIVATE_CHECKOUTS": 7,
            "PAGE_LIMIT": 20,
            "MAX_SUMS_BYTES": 65536,
            "MAX_AGE_SECONDS": 15 * 60,
            "MAX_FUTURE_SKEW_SECONDS": 2 * 60,
            "MAX_ENVELOPE_BYTES": 64 * 1024,
            "MIN_BODY_BYTES": 1024,
            "MAX_BODY_BYTES": 1024 * 1024,
            "MAX_UPDATE_MANIFEST_BYTES": 1024 * 1024,
            "R2_PROOF_MAX_RESPONSE_BYTES": 256 * 1024,
            "MAX_LATENCY_MS": 20_000,
            "MINIMUM_FILES": 500,
            "MINIMUM_TESTS": 2044,
            "WORKERS": 4,
            "GROUP_FILE_CAP": 8,
            "GROUP_WEIGHT_BUDGET": 8,
            "TIMEOUT_EXIT_CODE": 124,
            "GH_READ_OUTPUT_LIMIT": 16 * 1024 * 1024,
            "GITLEAKS_VERSION": "8.30.1",
            "GITLEAKS_LINUX_X64_SHA256": (
                "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
            ),
            "GITLEAKS_RELEASE_BASE": (
                "https://github.com/gitleaks/gitleaks/releases/download"
            ),
            "GITLEAKS_DOCKER_IMAGE": "ubuntu:24.04",
            "GITLEAKS_CONTAINER_BIN": "/usr/local/bin/gitleaks",
            "GITLEAKS_DOWNLOAD_MAX_SECONDS": 120,
            "GITLEAKS_DOWNLOAD_ATTEMPTS": 3,
            "GITLEAKS_SCAN_TIMEOUT_SECONDS": 180,
            "GITLEAKS_ABSENT_PUSH_BEFORE": "0" * 40,
        }
        for name, value in expected.items():
            self.assertEqual(getattr(constants, name), value, name)

    def test_rendered_endpoints_match_the_previous_bytes(self) -> None:
        constants = _load(CONSTANTS_PATH, "ci_constants_rendered")
        self.assertEqual(
            constants.YUELINK_PRIVATE_REPOSITORY,
            "https://github.com/onesyue/yuelink.git",
        )
        self.assertEqual(
            f"fetch {constants.YUELINK_PRIVATE_REPOSITORY}",
            "fetch https://github.com/onesyue/yuelink.git",
        )
        self.assertEqual(
            f"--cert-oidc-issuer {constants.ACTIONS_OIDC_ISSUER}",
            "--cert-oidc-issuer https://token.actions.githubusercontent.com",
        )
        self.assertEqual(
            f"--predicate-type {constants.SLSA_PROVENANCE_PREDICATE}",
            "--predicate-type https://slsa.dev/provenance/v1",
        )
        self.assertEqual(
            f"{constants.CLOUDFLARE_API_ORIGIN}/client/v4/accounts/example/r2/buckets/example/lock",
            "https://api.cloudflare.com/client/v4/accounts/example/r2/buckets/example/lock",
        )
        self.assertEqual(
            f"http.lowSpeedLimit={constants.SOURCE_FETCH_LOW_SPEED_BYTES_PER_SECOND}",
            "http.lowSpeedLimit=1000",
        )
        self.assertEqual(
            f"http.lowSpeedTime={constants.SOURCE_FETCH_LOW_SPEED_SECONDS}",
            "http.lowSpeedTime=60",
        )
        self.assertEqual(constants.PUBLIC_TAG_VERIFY_ATTEMPTS, 12)
        self.assertEqual(str(constants.PUBLIC_TAG_VERIFY_INTERVAL_SECONDS), "5")
        self.assertEqual(
            f"--kill-after={constants.COVERAGE_GROUP_KILL_AFTER_SECONDS}s "
            f"{constants.COVERAGE_GROUP_DEADLINE_SECONDS}",
            "--kill-after=30s 300",
        )

    def test_sparse_checkout_scripts_keep_their_own_literals(self) -> None:
        """These two jobs check out one script and no constants module."""
        checker = _load(
            ROOT / "scripts" / "check-mihomo-upgrade-compat.py",
            "mihomo_literals",
        )
        self.assertEqual(
            checker.UPSTREAM_URL, "https://github.com/MetaCubeX/mihomo.git"
        )
        self.assertEqual(checker.FORK_URL, "https://github.com/onesyue/mihomo.git")
        mirror = (ROOT / "scripts" / "select-ubuntu-https-mirror.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('Path("/etc/apt/apt-mirrors.txt")', mirror)
        self.assertIn('"--max-time", "8"', mirror)

    def test_ci_check_env_override_reads_the_named_defaults(self) -> None:
        module = _load(ROOT / "scripts" / "ci_check.py", "ci_check_literals")
        saved = {name: os.environ.get(name) for name in ("CI_CHECK_ATTEMPTS", "CI_CHECK_INTERVAL")}
        try:
            os.environ.pop("CI_CHECK_ATTEMPTS", None)
            os.environ.pop("CI_CHECK_INTERVAL", None)
            self.assertEqual(module.attempt_budget(), (30, 20.0))
            os.environ["CI_CHECK_ATTEMPTS"] = "4"
            os.environ["CI_CHECK_INTERVAL"] = "3"
            self.assertEqual(module.attempt_budget(), (4, 3.0))
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_coverage_worker_cap_reads_the_named_default(self) -> None:
        module = _load(ROOT / "scripts" / "flutter_coverage" / "runner.py", "coverage_literals")
        snapshot = {
            "effective_cpus": 8,
            "available_memory_bytes": 64 * 1024 ** 3,
            "capacity_status": "VISIBLE_BOUNDS",
        }
        saved = os.environ.get("YUELINK_COVERAGE_MAX_WORKERS")
        try:
            os.environ.pop("YUELINK_COVERAGE_MAX_WORKERS", None)
            self.assertEqual(module.WORKERS, 4)
            self.assertEqual(module.worker_count(snapshot), 4)
            os.environ["YUELINK_COVERAGE_MAX_WORKERS"] = "2"
            self.assertEqual(module.worker_count(snapshot), 2)
        finally:
            if saved is None:
                os.environ.pop("YUELINK_COVERAGE_MAX_WORKERS", None)
            else:
                os.environ["YUELINK_COVERAGE_MAX_WORKERS"] = saved


    def test_call_sites_import_the_moved_literals(self) -> None:
        constants = _load(CONSTANTS_PATH, "moved_literal_constants")
        manifest_source = (ROOT / "scripts" / "verify-update-manifest.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("timedelta(days=45)", manifest_source)
        self.assertNotIn("timedelta(hours=6)", manifest_source)
        self.assertIn(
            'timedelta(seconds=_LIMITS["MAX_MANIFEST_LIFETIME_SECONDS"])',
            manifest_source,
        )
        self.assertIn(
            'timedelta(seconds=_LIMITS["MAX_PUBLISH_CLOCK_SKEW_SECONDS"])',
            manifest_source,
        )
        manifest = _load(
            ROOT / "scripts" / "verify-update-manifest.py", "moved_manifest"
        )
        self.assertEqual(
            manifest.MAX_MANIFEST_LIFETIME.total_seconds(),
            constants.MAX_MANIFEST_LIFETIME_SECONDS,
        )
        self.assertEqual(
            manifest.MAX_PUBLISH_CLOCK_SKEW.total_seconds(),
            constants.MAX_PUBLISH_CLOCK_SKEW_SECONDS,
        )

        pin_source = (ROOT / "scripts" / "reconcile-yuelink-pin.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("1024 * 1024", pin_source)
        self.assertNotIn("timedelta(hours=6)", pin_source)
        self.assertIn('MAX_BYTES = _LIMITS["MAX_UPDATE_MANIFEST_BYTES"]', pin_source)
        self.assertIn(
            'timedelta(seconds=_LIMITS["MAX_PUBLISH_CLOCK_SKEW_SECONDS"])',
            pin_source,
        )
        pin = _load(ROOT / "scripts" / "reconcile-yuelink-pin.py", "moved_pin")
        self.assertEqual(pin.MAX_BYTES, constants.MAX_UPDATE_MANIFEST_BYTES)
        self.assertEqual(
            pin._PUBLISH_CLOCK_SKEW.total_seconds(),
            constants.MAX_PUBLISH_CLOCK_SKEW_SECONDS,
        )

        drift_source = (ROOT / "scripts" / "release_drift.py").read_text(encoding="utf-8")
        self.assertNotIn("24 * 60 * 60", drift_source)
        self.assertIn('_LIMITS["UNTAGGED_SHIPPED_SOURCE_SECONDS"]', drift_source)

        shell = (ROOT / "scripts" / "create-r2-lock-attestation.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("fetch_with_budget 30 3 10", shell)
        self.assertNotIn("--max-filesize 262144", shell)
        self.assertIn("fetch_with_budget 60 4 15", shell)
        for name in (
            "R2_LOCK_FETCH_BUDGET_SECONDS",
            "R2_LOCK_FETCH_ATTEMPTS",
            "R2_LOCK_FETCH_ATTEMPT_CAP_SECONDS",
            "R2_PROOF_MAX_RESPONSE_BYTES",
        ):
            self.assertIn(f'constants.py" {name}', shell)
            self.assertIn(f'"${name}"', shell)
            self.assertEqual(
                int(getattr(constants, name)),
                {
                    "R2_LOCK_FETCH_BUDGET_SECONDS": 30,
                    "R2_LOCK_FETCH_ATTEMPTS": 3,
                    "R2_LOCK_FETCH_ATTEMPT_CAP_SECONDS": 10,
                    "R2_PROOF_MAX_RESPONSE_BYTES": 262144,
                }[name],
            )
if __name__ == "__main__":
    current = scan()
    print(json.dumps(current, indent=2, sort_keys=True))
    print("\n".join(getattr(scan, "notes", [])))
