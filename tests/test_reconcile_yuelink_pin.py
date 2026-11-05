"""Include the standalone reconciliation tests in policy-ci unittest discovery."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pin_plan_tests",
    Path(__file__).resolve().parents[1] / "scripts/test-reconcile-yuelink-pin.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
PinPlanTests = module.PinPlanTests
