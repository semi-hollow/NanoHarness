from dataclasses import dataclass


@dataclass(frozen=True)
class ToolRoute:
    """Candidate tools selected for one LLM turn."""

    # Schemas actually sent to the model this turn.
    schemas: list[dict]

    # Tool names allowed after routing. Trace uses this to explain omissions.
    allowed_names: set[str]

    # Short human-readable routing explanation.
    reason: str

    # Tools withheld from the model to reduce overload or risk.
    dropped_names: list[str]

    # Capability/risk/latency tags for each exposed tool.
    metadata: dict[str, dict]

    def policy_summary(self) -> dict[str, object]:
        """Return report-friendly tool routing facts for this turn."""

        return {
            "allowed_tools": sorted(self.allowed_names),
            "hidden_tools": list(self.dropped_names),
            "tool_count": {"allowed": len(self.allowed_names), "hidden": len(self.dropped_names)},
            "metadata": self.metadata,
        }


class ToolRouter:
    """Route a large tool catalog down to task-relevant candidates.

    The local project has only a handful of tools, but the design mirrors a
    production tool gateway: classify tools by capability/risk, keep read-only
    discovery cheap, and expose write/command tools only when the task needs
    them. This is the answer to "what if there are 100+ APIs?"
    """

    DEFAULT_METADATA = {
        "list_files": {"capability": "discover", "risk": "low", "latency": "low", "mode": "read"},
        "read_file": {"capability": "inspect", "risk": "low", "latency": "low", "mode": "read"},
        "grep": {"capability": "search", "risk": "low", "latency": "low", "mode": "read"},
        "grep_search": {"capability": "search", "risk": "low", "latency": "low", "mode": "read"},
        "diagnostics": {"capability": "validate", "risk": "low", "latency": "medium", "mode": "read"},
        "git_status": {"capability": "diff", "risk": "low", "latency": "low", "mode": "read"},
        "git_diff": {"capability": "diff", "risk": "low", "latency": "low", "mode": "read"},
        "ask_human": {"capability": "clarify", "risk": "low", "latency": "human", "mode": "human"},
        "apply_patch": {"capability": "edit", "risk": "medium", "latency": "low", "mode": "write"},
        "write_file": {"capability": "edit", "risk": "high", "latency": "low", "mode": "write"},
        "run_command": {"capability": "validate", "risk": "high", "latency": "medium", "mode": "command"},
    }

    def route(
        self,
        task: str,
        schemas: list[dict],
        *,
        step: int = 1,
        agent_name: str = "",
        skill_tool_names: set[str] | None = None,
    ) -> ToolRoute:
        """Return routed tool schemas with explainable metadata."""

        lowered = (task or "").lower()
        by_name = {schema.get("name", ""): schema for schema in schemas}
        names = set(by_name)
        read_only_markers = [
            "不要修改",
            "不修改",
            "不要改",
            "不改",
            "只读",
            "仅阅读",
            "do not modify",
            "do not edit",
            "read only",
            "without editing",
        ]
        read_only_requested = any(marker in lowered for marker in read_only_markers)
        if read_only_requested and any(
            marker in lowered
            for marker in [
                "do not edit tests unless",
                "do not modify tests unless",
                "不要修改测试，除非",
                "不要改测试，除非",
            ]
        ):
            read_only_requested = False

        # Discovery tools are always useful and safe. They keep context/tool
        # selection robust even when the task text is short.
        selected = {
            name
            for name in names
            if self.DEFAULT_METADATA.get(name, {}).get("capability") in {"discover", "inspect", "search"}
        }

        if not read_only_requested and any(
            token in lowered for token in ["fix", "repair", "resolve", "patch", "implement", "修复", "实现", "补充"]
        ):
            selected |= names & {"apply_patch", "write_file", "run_command", "diagnostics", "git_status", "git_diff"}
        if not read_only_requested and any(token in lowered for token in ["test", "validate", "验证", "测试", "unittest"]):
            selected |= names & {"run_command", "diagnostics"}
        if any(token in lowered for token in ["review", "diff", "审查", "回滚"]):
            selected |= names & {"git_diff", "git_status", "read_file"}
        if any(token in lowered for token in ["clarify", "unclear", "ambiguous", "澄清", "不明确"]):
            selected |= names & {"ask_human"}

        # Active Skills are allowed to widen the routed tool set. This is the
        # practical difference between a passive Skill manifest and a runtime
        # Skill: once selected, its expected tools become available this turn.
        if skill_tool_names:
            selected |= names & skill_tool_names
        if read_only_requested:
            selected -= {"apply_patch", "write_file", "run_command"}

        # SWE-bench cases routinely tempt models into shell snippets, pipes,
        # redirection, `/tmp` scratch files, or helper scripts. Those actions
        # are either blocked or waste scarce benchmark steps. For benchmark
        # repair, keep validation available through diagnostics and edits
        # available through apply_patch, but hide general write_file/run_command.
        if "swe-bench" in lowered or "swebench" in lowered:
            selected -= {"run_command", "write_file"}
            selected |= names & {"apply_patch", "diagnostics", "git_diff", "git_status"}

        external_names = names - set(self.DEFAULT_METADATA)
        task_terms = {term for term in lowered.replace("_", " ").replace(".", " ").split() if len(term) >= 3}
        for name in external_names:
            schema = by_name[name]
            searchable = f"{name} {schema.get('description', '')}".lower()
            if "mcp" in lowered or "tool" in lowered or "policy" in lowered or any(term in searchable for term in task_terms):
                selected.add(name)

        # Role allowlists may leave no discovery tool. In that case preserve the
        # role's entire view; the allowlist is already a stricter boundary.
        if not selected:
            selected = set(names)

        routed = [schema for schema in schemas if schema.get("name") in selected]
        dropped = sorted(names - selected)
        reason = (
            f"selected={len(routed)} dropped={len(dropped)} step={step} "
            f"agent={agent_name or 'agent'} skill_tools={len(skill_tool_names or set())}"
        )
        return ToolRoute(
            schemas=routed,
            allowed_names={schema.get("name", "") for schema in routed},
            reason=reason,
            dropped_names=dropped,
            metadata={
                name: self.DEFAULT_METADATA.get(
                    name,
                    {"capability": "external", "risk": "configured", "latency": "unknown", "mode": "mcp_style"},
                )
                for name in sorted(selected)
            },
        )
