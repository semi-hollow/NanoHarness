"""NanoHarness 嵌入式运行控制器。"""

from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock

from agent_forge.runtime.domain.run_control import RunControlKind, RunControlSignal


class RunController:
    """线程安全地向同步 ``Harness.run`` 提交协作式控制信号。

    不传 ``run_id`` 的信号会交给下一次轮询它的 run，适合一个 Harness 对应一个前台
    任务的场景；并发运行时应显式传入从 ``run.started`` 事件取得的 run id。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._terminal: dict[str, RunControlSignal] = {}
        self._steers: dict[str, deque[RunControlSignal]] = defaultdict(deque)

    # 主要入口：请求 AgentLoop 在下一个安全边界暂停并保存 checkpoint。
    def pause(self, reason: str = "operator requested pause", *, run_id: str = "") -> None:
        """提交 pause；不会中断正在执行的单个外部调用。"""

        self._set_terminal(
            run_id,
            RunControlSignal(RunControlKind.PAUSE, reason=reason),
        )

    # 主要入口：请求 AgentLoop 在下一个安全边界取消，不回滚既有副作用。
    def cancel(self, reason: str = "operator requested cancel", *, run_id: str = "") -> None:
        """提交 cancel；已完成副作用仍由 operation ledger 保留。"""

        self._set_terminal(
            run_id,
            RunControlSignal(RunControlKind.CANCEL, reason=reason),
        )

    # 主要入口：把新的用户方向注入下一次模型调用，不修改已经执行的事实。
    def steer(self, message: str, *, run_id: str = "") -> None:
        """提交 steer，并在下一次安全模型边界追加为用户消息。"""

        signal = RunControlSignal(RunControlKind.STEER, message=message)
        with self._lock:
            self._steers[run_id].append(signal)

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        """优先消费指定 run 的信号，再消费未绑定信号。"""

        with self._lock:
            return self._terminal.pop(run_id, None) or self._terminal.pop("", None)

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        """保持提交顺序合并指定 run 与未绑定 steer。"""

        with self._lock:
            signals = list(self._steers.pop(run_id, ()))
            signals.extend(self._steers.pop("", ()))
            return sorted(signals, key=lambda item: item.requested_at)

    def _set_terminal(self, run_id: str, signal: RunControlSignal) -> None:
        with self._lock:
            current = self._terminal.get(run_id)
            if current is not None and current.kind == RunControlKind.CANCEL:
                return
            self._terminal[run_id] = signal
