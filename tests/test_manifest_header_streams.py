"""Run the native watchdog header predicates with large CRLF input."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/manifest-health.yml'
HEADERS = {
    'location': 'location: https://yuetong.app/update.json',
    'strict-transport-security': 'strict-transport-security: max-age=15552000; includeSubDomains',
    'x-content-type-options': 'x-content-type-options: nosniff',
}


class ManifestHeaderStreamTests(unittest.TestCase):
    def predicates(self) -> dict[str, str]:
        source = WORKFLOW.read_text()
        found = re.findall(r"tr -d '\\r' < \"\$(?:http|https)_headers\" \| grep -E(?:qi|i) \\\n\s+'[^']+'(?: >/dev/null)?", source)
        self.assertEqual(len(found), 3, 'all three real HTTP header checks must be exercised')
        return {name: next(value for value in found if "'^" + name + ':' in value) for name in HEADERS}

    def invoke(self, command: str, data: str | None, *, failed_transform: bool = False) -> int:
        with tempfile.TemporaryDirectory() as directory:
            headers = Path(directory) / 'headers'
            if data is not None:
                headers.write_bytes(data.encode())
            env = dict(os.environ, http_headers=str(headers), https_headers=str(headers))
            prefix = 'tr() { command tr "$@"; return 23; }\n' if failed_transform else ''
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', prefix + command],
                                    env=env, capture_output=True, text=True, timeout=10)
            return result.returncode

    def test_large_valid_headers_remain_accepted(self) -> None:
        for name, command in self.predicates().items():
            with self.subTest(header=name):
                data = 'HTTP/2 200\r\n' + HEADERS[name] + '\r\n' + ('x-padding: ' + 'x' * 150 + '\r\n') * 65536
                self.assertEqual(self.invoke(command, data), 0)

    def test_absent_wrong_empty_missing_and_transform_failure_stay_rejected(self) -> None:
        for name, command in self.predicates().items():
            for data in ('HTTP/2 200\r\nx-other: value\r\n', name + ': wrong\r\n', '', None):
                with self.subTest(header=name, input=data):
                    self.assertNotEqual(self.invoke(command, data), 0)
            with self.subTest(header=name, error='transform'):
                self.assertNotEqual(self.invoke(command, HEADERS[name] + '\r\n', failed_transform=True), 0)


if __name__ == '__main__':
    unittest.main()
