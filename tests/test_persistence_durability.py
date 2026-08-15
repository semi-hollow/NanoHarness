from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.atomic_json import atomic_write_json
from agent_forge.observability.adapters.json_trace import (
    JsonTraceRecorder,
    read_trace_jsonl,
)


class AtomicJsonTest(unittest.TestCase):
    def test_write_fsyncs_temp_then_replaces_then_fsyncs_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state.json"
            calls: list[str] = []
            real_replace = os.replace

            def replace(source: str | Path, destination: str | Path) -> None:
                calls.append("replace")
                self.assertEqual(Path(source).parent, path.parent)
                real_replace(source, destination)

            with (
                patch(
                    "agent_forge.atomic_json.os.fsync",
                    side_effect=lambda _descriptor: calls.append("fsync"),
                ),
                patch(
                    "agent_forge.atomic_json.os.replace",
                    side_effect=replace,
                ),
            ):
                atomic_write_json(path, {"schema_version": 2, "value": "new"})

            self.assertEqual(calls, ["fsync", "replace", "fsync"])
            self.assertEqual(json.loads(path.read_text()), {"schema_version": 2, "value": "new"})
            self.assertEqual(list(path.parent.glob(".state.json.*.tmp")), [])

    def test_failed_replace_preserves_old_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state.json"
            path.write_text('{"value": "old"}\n', encoding="utf-8")

            with (
                patch(
                    "agent_forge.atomic_json.os.replace",
                    side_effect=OSError("simulated crash boundary"),
                ),
                self.assertRaises(OSError),
            ):
                atomic_write_json(path, {"value": "new"})

            self.assertEqual(json.loads(path.read_text()), {"value": "old"})
            self.assertEqual(list(path.parent.glob(".state.json.*.tmp")), [])


class TraceJournalTest(unittest.TestCase):
    def test_event_is_flushed_before_final_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.json"
            recorder = JsonTraceRecorder(str(path))
            recorder.add(1, "agent", "model_request", prompt="bounded")

            _context, events, truncated = read_trace_jsonl(recorder.journal_path)

            self.assertFalse(truncated)
            self.assertEqual(events[0]["event_type"], "model_request")
            self.assertFalse(path.exists())
            recorder.close()

    def test_projection_reads_journal_instead_of_mutable_event_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.json"
            recorder = JsonTraceRecorder(str(path))
            recorder.add(1, "agent", "model_response", response="recorded")
            recorder.events.clear()

            recorder.publish()

            projection = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(projection["schema_version"], 2)
            self.assertEqual(projection["events"][0]["event_type"], "model_response")

    def test_reader_ignores_only_unterminated_corrupt_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.json"
            recorder = JsonTraceRecorder(str(path))
            recorder.add(1, "agent", "model_request")
            recorder.close()
            journal = Path(recorder.journal_path)
            with journal.open("ab") as handle:
                handle.write(b'{"record_type":"event"')

            _context, events, truncated = read_trace_jsonl(journal)

            self.assertTrue(truncated)
            self.assertEqual(len(events), 1)

    def test_reader_rejects_corrupt_middle_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trace.json"
            recorder = JsonTraceRecorder(str(path))
            recorder.add(1, "agent", "model_request")
            recorder.close()
            journal = Path(recorder.journal_path)
            lines = journal.read_bytes().splitlines(keepends=True)
            journal.write_bytes(lines[0] + b"{broken}\n" + lines[1])

            with self.assertRaisesRegex(ValueError, "line 2"):
                read_trace_jsonl(journal)


if __name__ == "__main__":
    unittest.main()
