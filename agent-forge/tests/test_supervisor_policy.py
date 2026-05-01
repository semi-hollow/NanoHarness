import unittest

from agent_forge.agents.supervisor_phase import TaskPhase
from agent_forge.agents.supervisor_policy import SupervisorPolicy


class TestSupervisorPolicy(unittest.TestCase):
    def test_initial_to_planning(self):
        self.assertEqual(SupervisorPolicy().decide_next_phase({}), TaskPhase.PLANNING)

    def test_plan_done_to_coding(self):
        state = {"phase": TaskPhase.PLANNING.value, "plan": "plan"}
        self.assertEqual(SupervisorPolicy().decide_next_phase(state), TaskPhase.CODING)

    def test_test_failed_retry(self):
        state = {"phase": TaskPhase.TESTING.value, "test_pass": False, "retry_count": 0}
        self.assertEqual(SupervisorPolicy(max_retry=1).decide_next_phase(state), TaskPhase.CODING)

    def test_retry_exhausted_failed(self):
        state = {"phase": TaskPhase.TESTING.value, "test_pass": False, "retry_count": 1}
        self.assertEqual(SupervisorPolicy(max_retry=1).decide_next_phase(state), TaskPhase.FAILED)

    def test_test_pass_to_reviewing(self):
        state = {"phase": TaskPhase.TESTING.value, "test_pass": True}
        self.assertEqual(SupervisorPolicy().decide_next_phase(state), TaskPhase.REVIEWING)

    def test_review_approved_to_done(self):
        state = {"phase": TaskPhase.REVIEWING.value, "review": "review=approved"}
        self.assertEqual(SupervisorPolicy().decide_next_phase(state), TaskPhase.DONE)

    def test_review_rejected_retry(self):
        state = {"phase": TaskPhase.REVIEWING.value, "review": "changes required", "retry_count": 0}
        self.assertEqual(SupervisorPolicy(max_retry=1).decide_next_phase(state), TaskPhase.CODING)

    def test_safety_blocked_failed(self):
        self.assertEqual(SupervisorPolicy().decide_next_phase({"safety_blocked": True}), TaskPhase.FAILED)
