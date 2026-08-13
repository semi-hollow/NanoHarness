import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import publish_runtime_evidence_view


class RuntimeEvidenceFilesTest(unittest.TestCase):
    def test_each_run_keeps_an_independent_view_and_latest_moves_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            control_root = workspace / ".agent_forge"

            first = self._create_run(
                workspace,
                run_directory="run-001",
                run_id="runtime-001",
                task="repair payment retry",
                operation_key="operation-001",
                request_id="request-001",
            )
            first_view = self._publish(workspace, first)

            second = self._create_run(
                workspace,
                run_directory="run-002",
                run_id="runtime-002",
                task="repair settlement rounding",
                operation_key="operation-002",
                request_id="request-002",
            )
            second_view = self._publish(workspace, second)

            evidence_root = control_root / "runtime_evidence"
            self.assertTrue(first_view.is_dir())
            self.assertTrue(second_view.is_dir())
            self.assertNotEqual(first_view, second_view)
            self.assertEqual((evidence_root / "latest").resolve(), second_view.resolve())

            catalog = (evidence_root / "INDEX.md").read_text(encoding="utf-8")
            self.assertIn(first_view.name, catalog)
            self.assertIn(second_view.name, catalog)

            # 第二个 run 的视图不能混入第一个 run 的审批和账本。
            second_approvals = list((second_view / "03_approval").glob("*.json"))
            second_ledgers = list((second_view / "04_operation_ledger").glob("*.json"))
            self.assertEqual([path.name for path in second_approvals], ["operation-002.json"])
            self.assertEqual([path.name for path in second_ledgers], ["operation-002.json"])

    def test_view_links_to_authoritative_files_instead_of_copying_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            run_dir = self._create_run(
                workspace,
                run_directory="run-approval",
                run_id="runtime-approval",
                task="approve one governed write",
                operation_key="operation-approved",
                request_id="request-answered",
            )

            view = self._publish(workspace, run_dir)
            approval_link = view / "03_approval" / "operation-approved.json"
            checkpoint_link = view / "01_checkpoint" / "checkpoint.json"
            self.assertTrue(approval_link.is_symlink())
            self.assertTrue(checkpoint_link.is_symlink())

            approval_source = (
                workspace / ".agent_forge" / "approvals" / "operation-approved.json"
            )
            approval_payload = json.loads(approval_source.read_text(encoding="utf-8"))
            approval_payload["status"] = "approved"
            self._write_json(approval_source, approval_payload)
            self.assertEqual(
                json.loads(approval_link.read_text(encoding="utf-8"))["status"],
                "approved",
            )

            readme = (view / "README.md").read_text(encoding="utf-8")
            self.assertIn("不是副本", readme)
            self.assertIn("每个 operation_key 一个 JSON", readme)
            self.assertIn("生命周期状态", readme)
            self.assertNotIn("恢复整次 run 的消息", readme)
            self.assertIn("Candidate Diff 只证明产生候选改动", readme)

    def _publish(self, workspace: Path, run_dir: Path) -> Path:
        control_root = workspace / ".agent_forge"
        return publish_runtime_evidence_view(
            workspace=workspace,
            run_dir=run_dir,
            approval_root=control_root / "approvals",
            human_input_root=control_root / "human_input",
            operation_ledger_root=control_root / "operation_ledger",
        )

    def _create_run(
        self,
        workspace: Path,
        *,
        run_directory: str,
        run_id: str,
        task: str,
        operation_key: str,
        request_id: str,
    ) -> Path:
        run_dir = workspace / "runs" / run_directory
        checkpoint_path = run_dir / "task_state" / f"{run_id}.json"
        self._write_json(
            checkpoint_path,
            {
                "run_id": run_id,
                "task": task,
                "status": "waiting_approval",
                "stop_reason": "waiting_approval",
                "metadata": {"human_input_request_id": request_id},
            },
        )
        self._write_json(
            run_dir / "trace.json",
            {
                "run_id": run_id,
                "task": task,
                "stop_reason": "waiting_approval",
                "events": [
                    {
                        "event_type": "human_approval",
                        "operation_key": operation_key,
                    },
                    {
                        "event_type": "human_input_requested",
                        "human_input_request_id": request_id,
                    },
                ],
            },
        )
        self._write_json(
            run_dir / "run_manifest.json",
            {
                "run_id": run_id,
                "task": task,
                "status": "waiting_approval",
                "stop_reason": "waiting_approval",
                "artifacts": [],
            },
        )
        (run_dir / "candidate_changes.diff").write_text("", encoding="utf-8")

        control_root = workspace / ".agent_forge"
        self._write_json(
            control_root / "approvals" / f"{operation_key}.json",
            {
                "operation_key": operation_key,
                "run_id": run_id,
                "status": "pending",
            },
        )
        self._write_json(
            control_root / "operation_ledger" / f"{operation_key}.json",
            {
                "operation_key": operation_key,
                "run_id": run_id,
                "status": "pending",
                "history": ["planned", "pending"],
            },
        )
        self._write_json(
            control_root / "human_input" / f"{request_id}.json",
            {
                "request_id": request_id,
                "run_id": run_id,
                "status": "pending",
            },
        )
        self._write_json(
            control_root / "approvals" / "unrelated-operation.json",
            {
                "operation_key": "unrelated-operation",
                "run_id": "another-run",
                "status": "pending",
            },
        )
        return run_dir

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
