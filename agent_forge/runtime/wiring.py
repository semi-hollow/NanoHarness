"""Runtime dependency wiring shared by CLI and benchmark runners."""

from __future__ import annotations

from agent_forge.models.gateway import ModelGateway, RetryPolicy
from agent_forge.runtime.llm_client import MockLLMClient, OpenAICompatibleLLMClient
from agent_forge.runtime.llm_config import LLMConfig
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.ask_human import AskHumanTool
from agent_forge.tools.diagnostics import DiagnosticsTool
from agent_forge.tools.git_diff import GitDiffTool
from agent_forge.tools.git_status import GitStatusTool
from agent_forge.tools.grep import GrepSearchTool, GrepTool
from agent_forge.tools.list_files import ListFilesTool
from agent_forge.tools.mcp_config import MCPConfigLoader
from agent_forge.tools.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.run_command import RunCommandTool
from agent_forge.tools.write_file import WriteFileTool


def build_registry(
    workspace: str,
    auto: bool,
    mcp_config_file: str | None = None,
    mcp_allowed_tools: list[str] | None = None,
) -> ToolRegistry:
    """Create the tool gateway used by AgentLoop.

    This is intentionally centralized. A benchmark runner, a normal repo run,
    and a future product surface should expose the same governed tools unless
    there is an explicit policy reason to differ.
    """

    sandbox = WorkspaceSandbox(workspace)
    registry = ToolRegistry()
    for tool in [
        ListFilesTool(sandbox),
        ReadFileTool(sandbox),
        WriteFileTool(sandbox, auto),
        GrepTool(sandbox),
        GrepSearchTool(sandbox),
        ApplyPatchTool(sandbox, auto),
        RunCommandTool(sandbox, auto),
        GitStatusTool(sandbox),
        GitDiffTool(sandbox),
        DiagnosticsTool(sandbox),
        AskHumanTool(auto),
    ]:
        registry.register(tool)
    if mcp_config_file:
        registry.mcp_config_report = MCPConfigLoader(sandbox).load_into(
            registry,
            mcp_config_file,
            allowed_tools=mcp_allowed_tools,
        )
    return registry


def build_llm(config: LLMConfig):
    """Instantiate the provider selected by resolved LLM config."""

    if config.provider == "mock":
        return ModelGateway(
            MockLLMClient("single"),
            provider="mock",
            model="mock-single",
            retry_policy=RetryPolicy(max_attempts=1),
        )
    if config.uses_openai_compatible_api:
        return ModelGateway(
            OpenAICompatibleLLMClient.from_config(config),
            provider=config.provider,
            model=config.model or "unknown",
            retry_policy=RetryPolicy(max_attempts=2),
        )
    raise ValueError(f"Unsupported LLM provider: {config.provider}")
