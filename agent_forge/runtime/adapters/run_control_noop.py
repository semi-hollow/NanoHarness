"""CLI 默认使用的无控制信号 Adapter。"""

from agent_forge.runtime.domain.run_control import RunControlSignal


class NoopRunControl:
    """不产生任何外部控制信号。"""

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        return None

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        return []
