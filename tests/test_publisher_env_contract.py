from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build.yml"
CALL = "bash scripts/ci/publish_immutable_r2.sh"
REQUIRED_ENV = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "R2_ENDPOINT",
    "R2_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "BUCKET",
    "TAG",
}


class PublisherEnvironmentContractTest(unittest.TestCase):
    def test_every_mirrored_publisher_call_has_full_environment(self) -> None:
        workflow = WORKFLOW.read_text()
        calls: list[tuple[str, set[str]]] = []
        for call_match in re.finditer(re.escape(CALL), workflow):
            prefix = workflow[: call_match.start()]
            step_start = prefix.rfind("\n      - name:")
            step = workflow[step_start : call_match.end()]
            name_match = re.search(r"^      - name: (.+)$", step, re.MULTILINE)
            env_names = set(
                re.findall(r"^          ([A-Z][A-Z0-9_]+):", step, re.MULTILINE)
            )
            calls.append(
                (name_match.group(1) if name_match else "<unnamed>", env_names)
            )

        self.assertEqual(len(calls), 2, calls)
        for step_name, environment in calls:
            with self.subTest(step=step_name):
                self.assertEqual(REQUIRED_ENV - environment, set())


if __name__ == "__main__":
    unittest.main()
