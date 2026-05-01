from enum import Enum


class TaskPhase(Enum):
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
