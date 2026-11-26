"""Machine-report, timeout, and LCOV row checks."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from flutter_coverage.settings import ENGINE_FAULT_EXIT_CODES, TIMEOUT_EXIT_CODE

ENGINE_SHUTDOWN_ERRORS = {
    "Shell subprocess crashed with segmentation fault",
    "Shell subprocess crashed with segmentation fault.",
    "getSourceReport: (112) Service has disappeared",
    "getSourceReport: (-32000) Service connection disposed",
}


def digest(value) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def relative(path: str, worker: Path, prefix: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        candidate = candidate.relative_to(worker)
    if ".." in candidate.parts or not str(candidate).startswith(prefix):
        raise ValueError(f"report path escaped {prefix}")
    return str(candidate)


def cases(report: Path, worker: Path, expected: list[str], counter) -> tuple[int, Counter]:
    lines = report.read_text().splitlines()
    count = counter.count_test_starts(lines)
    suites, started, finished, reached = {}, {}, set(), set()
    result = Counter()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # The canonical parser rejects corruption after start.
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "suite":
            suite = event["suite"]
            path = relative(suite["path"], worker, "test/")
            if suite["id"] in suites or path in suites.values() or path not in expected:
                raise ValueError("duplicate or unexpected suite identity")
            suites[suite["id"]] = path
        elif kind == "testStart":
            test = event["test"]
            if test["id"] in started or test["suiteID"] not in suites:
                raise ValueError("duplicate test or missing suite identity")
            started[test["id"]] = test
        elif kind == "testDone":
            identity = event["testID"]
            if identity not in started or identity in finished or event["result"] != "success":
                raise ValueError("missing/duplicate/failed test terminal")
            if type(event["hidden"]) is not bool or type(event["skipped"]) is not bool:
                raise ValueError("invalid visible/skip classification")
            finished.add(identity)
            if not event["hidden"]:
                test = started[identity]
                path = suites[test["suiteID"]]
                reached.add(path)
                result[(path, test["name"].replace(str(worker), "<source>"), event["skipped"])] += 1
    if finished != set(started) or reached != set(expected) or set(suites.values()) != set(expected):
        raise ValueError("requested file or test terminal missing")
    return count, result


def recoverable(exit_code: int, classification: str) -> bool:
    """Whether a nonzero group attempt may be re-executed in smaller parts."""
    if classification == "engine_shutdown_fault":
        return exit_code in ENGINE_FAULT_EXIT_CODES
    return exit_code == TIMEOUT_EXIT_CODE and classification == "no_recorded_failure"


def timeout_classification(report: Path) -> str:
    """Admit only known loading/coverage shutdown errors, never test failures."""
    suites, started = {}, {}
    engine_errors = 0
    for line in report.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "suite":
            suite = event.get("suite", {})
            suites[suite.get("id")] = suite.get("path")
        elif kind == "testStart":
            test = event.get("test", {})
            started[test.get("id")] = test
        elif kind == "testDone" and event.get("result") in {"failure", "error"}:
            return "recorded_failure"
        elif kind == "error":
            test = started.get(event.get("testID"), {})
            path = suites.get(test.get("suiteID"))
            if (not isinstance(path, str) or not path
                    or test.get("name") != "loading " + path
                    or event.get("error") not in ENGINE_SHUTDOWN_ERRORS
                    or event.get("isFailure") is not False):
                return "recorded_failure"
            engine_errors += 1
        elif kind == "done" and event.get("success") is False:
            # A failed summary can be explained only by an already observed
            # loading crash; any failed test terminal still vetoes recovery.
            if not engine_errors:
                return "recorded_failure"
    if engine_errors:
        return "engine_shutdown_fault"
    return "no_recorded_failure"


def coverage_rows(path: Path, worker: Path) -> set:
    rows = set()
    source = None
    for line in path.read_text().splitlines():
        if line.startswith("SF:"):
            source = relative(line[3:], worker, "lib/")
        elif line == "end_of_record":
            source = None
        elif line.startswith(("DA:", "BRDA:", "FNDA:")):
            if source is None:
                raise ValueError("coverage counter without source")
            kind, payload = line.split(":", 1)
            fields = payload.split(",")
            if kind == "DA":
                key, hits = fields[:2]
            elif kind == "BRDA":
                key, hits = ",".join(fields[:3]), fields[3]
            else:
                hits, key = payload.split(",", 1)
            rows.add((source, kind, key, False))
            if hits != "-" and int(hits) > 0:
                rows.add((source, kind, key, True))
    return rows
