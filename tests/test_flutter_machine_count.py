from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/flutter-machine-protocol.jsonl"
SPEC = importlib.util.spec_from_file_location(
    "count_flutter_machine_tests", ROOT / "scripts/count-flutter-machine-tests.py"
)
assert SPEC and SPEC.loader
counter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(counter)


class FlutterMachineCountTests(unittest.TestCase):
    def test_tool_preamble_and_array_protocol_events_are_accepted(self) -> None:
        with FIXTURE.open(encoding="utf-8") as stream:
            self.assertEqual(counter.count_test_starts(stream), 2)

    def test_malformed_record_after_start_fails_closed(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "malformed Flutter machine JSON"):
            counter.count_test_starts(
                io.StringIO('{"type":"start"}\n{"type":"testStart"\n')
            )

    def test_json_record_before_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "before Flutter protocol start"):
            counter.count_test_starts(
                io.StringIO('{"type":"testStart","test":{"id":1}}\n')
            )

    def test_missing_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "start record is missing"):
            counter.count_test_starts(io.StringIO("Resolving dependencies...\n"))

    def test_missing_done_is_rejected(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "done record is missing"):
            counter.count_test_starts(
                io.StringIO(
                    '{"type":"start"}\n'
                    '{"type":"testStart","test":{"id":1}}\n'
                )
            )

    def test_unsuccessful_done_is_rejected(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "without success=true"):
            counter.count_test_starts(
                io.StringIO('{"type":"start"}\n{"type":"done","success":false}\n')
            )

    def test_duplicate_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "duplicate Flutter protocol start"):
            counter.count_test_starts(
                io.StringIO('{"type":"start"}\n{"type":"start"}\n')
            )

    def test_record_after_done_is_rejected(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "after Flutter protocol done"):
            counter.count_test_starts(
                io.StringIO(
                    '{"type":"start"}\n'
                    '{"type":"done","success":true}\n'
                    '[{"event":"late"}]\n'
                )
            )

    def test_floor_accepts_equal_or_higher_count(self) -> None:
        counter.enforce_floor(2044, 2044)
        counter.enforce_floor(2045, 2044)

    def test_floor_rejects_a_single_missing_test(self) -> None:
        with self.assertRaisesRegex(counter.ProtocolError, "observed 2043"):
            counter.enforce_floor(2043, 2044)


if __name__ == "__main__":
    unittest.main()
