from enum import Enum


class TaskPhase(Enum):
    """Finite phases used by SupervisorPolicy and SupervisorAgent."""

    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
