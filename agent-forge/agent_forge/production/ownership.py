from dataclasses import dataclass, field


@dataclass
class OwnershipPlan:
    """File ownership contract for one scheduled agent run.

    Coding agents fail in practice when two workers edit the same file without a
    merge contract. This plan records which agent owns writes to each file and
    produces explicit conflicts before execution.
    """

    owners: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)

    def claim(self, agent_name: str, files: set[str]) -> None:
        """Assign write ownership and record conflicts instead of overwriting."""

        for path in sorted(files):
            owner = self.owners.get(path)
            if owner and owner != agent_name:
                self.conflicts.setdefault(path, [owner])
                if agent_name not in self.conflicts[path]:
                    self.conflicts[path].append(agent_name)
            else:
                self.owners[path] = agent_name

    def has_conflicts(self) -> bool:
        """Return whether ownership is ambiguous."""

        return bool(self.conflicts)

    def to_dict(self) -> dict:
        """Return JSON-safe ownership data for run reports."""

        return {"owners": self.owners, "conflicts": self.conflicts}
