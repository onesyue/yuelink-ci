"""Check secret declarations and forwarding for the builder's reusable workflows."""

from copy import deepcopy
from pathlib import Path
import re
import unittest

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
LOCAL_CALL = re.compile(
    r"(?:\./|onesyue/yuelink-ci/)\.github/workflows/([^/@]+)(?:@[^ ]+)?$"
)
SECRET_REFERENCE = re.compile(
    r"\bsecrets\s*(?:\.\s*([\w]+)|\[\s*'([\w]+)'\s*\])",
    re.IGNORECASE,
)
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}", re.DOTALL)

R2_SECRETS = {"R2_KEY_ID", "R2_APP_KEY", "CLOUDFLARE_R2_CONFIG_TOKEN"}
ENVIRONMENT_SECRETS = {
    "yuelink-build": R2_SECRETS | {
        "KEYSTORE_BASE64", "KEYSTORE_PASSWORD", "KEY_ALIAS", "KEY_PASSWORD",
        "WINDOWS_CERT_BASE64", "WINDOWS_CERT_PASSWORD",
        "APPLE_CERTIFICATE_BASE64", "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_SIGNING_IDENTITY", "APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID",
    },
    "yuelink-release": R2_SECRETS | {"UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64"},
}


def secret_references(node, *, bare_expression=False):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from secret_references(value, bare_expression=key == "if")
    elif isinstance(node, list):
        for value in node:
            yield from secret_references(value)
    elif isinstance(node, str):
        expressions = EXPRESSION.findall(node)
        if bare_expression and not expressions:
            expressions = [node]
        for expression in expressions:
            for dotted, indexed in SECRET_REFERENCE.findall(expression):
                yield (dotted or indexed).upper()


def contract_errors(workflows):
    declarations = {
        name: trigger["workflow_call"].get("secrets", {})
        for name, workflow in workflows.items()
        if "workflow_call" in (trigger := workflow.get("on", {}))
    }
    errors = []
    for name, workflow in workflows.items():
        jobs = workflow["jobs"]
        if name in declarations:
            declared = set(declarations[name])
            referenced = set(secret_references({
                key: value for key, value in workflow.items() if key != "jobs"
            }))
            for job in jobs.values():
                environment = job.get("environment", "")
                if isinstance(environment, dict):
                    environment = environment["name"]
                referenced.update(
                    set(secret_references(job))
                    - ENVIRONMENT_SECRETS.get(environment, set())
                )
            referenced.discard("GITHUB_TOKEN")  # Automatically available to callees.
            if declared != referenced:
                errors.append(
                    f"{name}: undeclared {sorted(referenced - declared)}, "
                    f"unused declarations {sorted(declared - referenced)}"
                )

        for job_name, job in jobs.items():
            mapping = job.get("secrets", {})
            caller = f"{name}/{job_name}"
            if mapping == "inherit":
                errors.append(f"{caller}: secrets inheritance")
                continue
            call = LOCAL_CALL.fullmatch(job.get("uses", ""))
            if not call:
                continue
            callee = call[1]
            if callee not in declarations:
                errors.append(f"{caller}: no reusable workflow {callee}")
                continue
            expected = set(declarations[callee])
            if not isinstance(mapping, dict):
                errors.append(f"{caller}: secrets must be a mapping")
                continue
            if expected != mapping.keys():
                errors.append(
                    f"{caller}: missing mappings {sorted(expected - mapping.keys())}, "
                    f"extra mappings {sorted(mapping.keys() - expected)}"
                )
    return errors


class ReusableWorkflowSecretsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader retains the workflow key "on" as a string.
        cls.workflows = {
            path.name: yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
            for path in sorted(WORKFLOWS.iterdir())
            if path.suffix in {".yml", ".yaml"}
        }

    def test_workflow_secret_contracts(self):
        self.assertEqual(contract_errors(self.workflows), [])

    def test_removing_a_mapping_breaks_the_contract(self):
        workflows = deepcopy(self.workflows)
        del workflows["build.yml"]["jobs"]["validate"]["secrets"]["SRC_DEPLOY_KEY"]
        self.assertEqual(
            contract_errors(workflows),
            ["build.yml/validate: missing mappings ['SRC_DEPLOY_KEY'], extra mappings []"],
        )
