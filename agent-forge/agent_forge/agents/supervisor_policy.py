from agent_forge.agents.supervisor_phase import TaskPhase


class SupervisorPolicy:
    def __init__(self, max_retry: int = 1):
        self.max_retry = max_retry

    def decide_next_phase(self, state: dict) -> TaskPhase:
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
