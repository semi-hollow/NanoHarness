from agent_forge.agents.supervisor_phase import TaskPhase


class SupervisorPolicy:
    """Decide the next supervised phase from shared multi-agent state.

    This is a tiny finite-state policy, not a production scheduler. It answers
    only one question: given the current demo state, should the supervisor move
    to coding, testing, reviewing, done, or failed? Industrial schedulers would
    also reason about task priority, dependency graphs, parallel workers,
    ownership conflicts, retry budgets, and human escalation.
    """

    def __init__(self, max_retry: int = 1):
        """Limit retries so a failed tester cannot create an infinite loop."""

        self.max_retry = max_retry

    def decide_next_phase(self, state: dict) -> TaskPhase:
        """Move planning -> coding -> testing -> reviewing/done/failed.

        The hard-coded phase order is intentional documentation-by-code for the
        study project. If you are explaining this in an interview, call it a
        minimal supervised workflow and then describe how it would evolve into a
        DAG scheduler.
        """

        if state.get("safety_blocked"):
            return TaskPhase.FAILED

        phase = state.get("phase")
        retry_count = int(state.get("retry_count", 0) or 0)

        if not phase:
            return TaskPhase.PLANNING
        if phase == TaskPhase.PLANNING.value and state.get("plan"):
            return TaskPhase.CODING
        if phase == TaskPhase.CODING.value and state.get("code_done"):
            return TaskPhase.TESTING
        if phase == TaskPhase.TESTING.value:
            if state.get("test_pass"):
                return TaskPhase.REVIEWING
            if retry_count < self.max_retry:
                return TaskPhase.CODING
            return TaskPhase.FAILED
        if phase == TaskPhase.REVIEWING.value:
            review = str(state.get("review", "")).lower()
            if "approved" in review:
                return TaskPhase.DONE
            if retry_count < self.max_retry:
                return TaskPhase.CODING
            return TaskPhase.FAILED

        return TaskPhase.PLANNING
