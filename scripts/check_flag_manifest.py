"""Compare workflow switches and updater defaults with policy/flags.json."""

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SWITCH_VALUES = {"0", "1", "true", "false", "yes", "no"}
SWITCH_NAME = re.compile(r"ENABLE|DISABLE|REQUIRE|SKIP|FORCE|_MODE", re.IGNORECASE)
VAR_REFERENCE = re.compile(r"\bvars\.([A-Za-z_][\w]*)\b", re.IGNORECASE)
VAR_BINDING = re.compile(
    r"\$\{\{\s*vars\.(\w+)\s*(?:\|\|\s*'((?:[^']|'')*)'\s*)?\}\}", re.IGNORECASE
)
SHELL_DEFAULT = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):-([^}]*)\}")
XCODE_SETTING = re.compile(r"(\w+_PREFIX_MAPPING|COMPILATION_CACHE_ENABLE_CACHING)\s*=\s*(.*)")


def entries(node):
    """Visit mapping entries and list items, including individual matrix values."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from entries(value)
    elif isinstance(node, list):
        for item in node:
            yield None, item
            yield from entries(item)


def run_settings(script):
    for line in script.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        for name, value in SHELL_DEFAULT.findall(line):
            yield "shell_defaults", name, value
        setting = XCODE_SETTING.fullmatch(line)
        if setting:
            yield "xcconfig", *setting.groups()


def workflow_settings(document, filename, expected, references):
    for key, value in entries(document):
        if isinstance(value, str):
            references.update(name.upper() for name in VAR_REFERENCE.findall(value))
        if key == "run" and isinstance(value, str):
            yield from run_settings(value)
        if key == "env" and isinstance(value, dict):
            for name, scalar in value.items():
                binding = VAR_BINDING.fullmatch(scalar) if isinstance(scalar, str) else None
                if binding and binding[1].upper() == name:
                    # GitHub supplies an empty string for an unset repository variable.
                    yield "var_env", name, (binding[2] or "").replace("''", "'")
                elif (filename, "var_env", name) in expected:
                    yield "var_env", name, None
                else:
                    yield "env", name, scalar


def script_defaults(path, rows):
    """Read literals and their argparse bindings without importing release code."""
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = {
        node.target.id: node.value
        for node in module.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    bindings = defaultdict(list)
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        for keyword in node.keywords:
            call = keyword.value
            if keyword.arg != "default" or not isinstance(call, ast.Call):
                continue
            if ast.unparse(call.func) != "os.environ.get" or not call.args:
                continue
            key = call.args[0]
            if isinstance(key, ast.Constant):
                fallback = call.args[1] if len(call.args) == 2 else None
                bindings[key.value].append(fallback.id if isinstance(fallback, ast.Name) else None)

    for name, row in rows.items():
        constant = row["constant"]
        literal = constants.get(constant)
        if not isinstance(literal, ast.Constant) or literal.value != row["script_default"]:
            yield f"{row['script']} {name}: {constant} differs from the reviewed default"
        if bindings[name] != [constant]:
            yield f"{row['script']} {name}: argument default must use {constant}"


def check(root):
    manifest = json.loads((root / "policy/flags.json").read_text(encoding="utf-8"))
    expected = {
        (workflow, source, name): value
        for workflow, sources in manifest["workflows"].items()
        for source, settings in sources.items()
        for name, value in settings.items()
    }
    variables = manifest["variables"]
    scripts = defaultdict(dict)
    for name, row in variables.items():
        for workflow in row["env_workflows"]:
            expected[workflow, "var_env", name] = row["workflow_default"]
        if "script" in row:
            scripts[row["script"]][name] = row

    seen = set()
    used_vars = set()
    errors = []
    paths = sorted((root / ".github/workflows").glob("*.yml"))
    if not paths:
        errors.append("no workflow YAML files found")
    for path in paths:
        # BaseLoader preserves literal spelling, including YES, false and quoted 1.
        try:
            document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        except yaml.YAMLError:
            errors.append(f"{path.name}: invalid workflow YAML")
            continue
        references = set()
        for source, name, value in workflow_settings(document, path.name, expected, references):
            location = (path.name, source, name)
            if location in expected:
                seen.add(location)
                if value != expected[location]:
                    errors.append(f"{' '.join(location)}: differs from the reviewed value")
            elif (
                source in {"var_env", "xcconfig"}
                or SWITCH_NAME.search(name)
                or isinstance(value, str) and value.lower() in SWITCH_VALUES
            ):
                errors.append(f"{' '.join(location)}: missing manifest row")
        for name in sorted(references - variables.keys()):
            errors.append(f"{path.name} vars.{name}: missing manifest row")
        used_vars.update(references)

    for location in sorted(expected.keys() - seen):
        errors.append(f"{' '.join(location)}: reviewed setting missing from workflow")
    for name in sorted(variables.keys() - used_vars):
        errors.append(f"vars.{name}: reviewed variable has no workflow reference")
    for script, rows in scripts.items():
        errors.extend(script_defaults(root / script, rows))
    return errors


if __name__ == "__main__":
    errors = check(ROOT)
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(bool(errors))
