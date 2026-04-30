from dataclasses import dataclass


@dataclass(frozen=True)
class StopDecision:
    should_stop: bool
    reason: str


def check_stop(iteration: int, max_iterations: int, consecutive_failures: int, final_answer: str = "") -> StopDecision:
    if final_answer:
        return StopDecision(True, "final_answer")
    if consecutive_failures >= 3:
        return StopDecision(True, "too_many_failed_tools")
    if iteration >= max_iterations:
        return StopDecision(True, "max_iterations")
    return StopDecision(False, "continue")
