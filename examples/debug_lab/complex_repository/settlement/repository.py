"""练习场景使用的内存仓储；接口形状接近真实 Repository。"""

from __future__ import annotations

from copy import deepcopy

from .domain import LedgerEntry, SettlementAccount


class InMemorySettlementRepository:
    """保存账户、已处理事件和账本，并支持测试读取状态快照。"""

    def __init__(self, accounts: list[SettlementAccount]) -> None:
        self._accounts = {account.settlement_id: account for account in accounts}
        self._processed_operation_keys: set[str] = set()
        self._ledger_entries: list[LedgerEntry] = []

    def get_account(self, settlement_id: str) -> SettlementAccount:
        try:
            return self._accounts[settlement_id]
        except KeyError as exc:
            raise ValueError(f"unknown settlement: {settlement_id}") from exc

    def was_processed(self, operation_key: str) -> bool:
        return operation_key in self._processed_operation_keys

    def mark_processed(self, operation_key: str) -> None:
        self._processed_operation_keys.add(operation_key)

    def append_ledger_entry(self, entry: LedgerEntry) -> None:
        self._ledger_entries.append(entry)

    def ledger_entries(self) -> list[LedgerEntry]:
        return list(self._ledger_entries)

    def snapshot(self) -> tuple[dict[str, SettlementAccount], set[str], list[LedgerEntry]]:
        """返回深拷贝快照，供原子性回归测试比较执行前后状态。"""

        return (
            deepcopy(self._accounts),
            set(self._processed_operation_keys),
            list(self._ledger_entries),
        )
