"""Built-in MCP tools for project policy and optional web lookup.

The web tools are intentionally provider-gated. By default they run in
``offline`` mode so company machines can verify the MCP path without touching
the network. Setting ``AGENT_FORGE_MCP_ALLOW_NETWORK=1`` plus
``AGENT_FORGE_WEB_PROVIDER`` enables real lookup through DuckDuckGo HTML,
OpenAI hosted web search, or Claude hosted web search.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_forge.mcp.server import MCPToolDefinition, MCPToolResult


NETWORK_ENABLE_VALUES = {"1", "true", "yes", "on"}


def build_builtin_tools(repo_root: Path | None = None) -> list[MCPToolDefinition]:
    """Return the default tools served by ``agent_forge.mcp.builtin_server``."""

    root = repo_root or Path(os.getenv("AGENT_FORGE_WORKSPACE", ".")).resolve()
    return [
        MCPToolDefinition(
            name="repo_policy",
            description=(
                "Read or search the repository policy file FORGE.md. Use this before "
                "touching files when a task mentions project rules, allowed commands, "
                "security constraints, or documentation expectations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Optional lowercase topic used to filter matching policy lines.",
                    }
                },
                "required": [],
            },
            handler=lambda args: _repo_policy(root, args),
        ),
        MCPToolDefinition(
            name="current_time",
            description="Return local and UTC time for timestamp-sensitive tasks.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda _args: _current_time(),
        ),
        MCPToolDefinition(
            name="web_fetch",
            description=(
                "Fetch one HTTP or HTTPS page and return readable text. Network is blocked "
                "unless AGENT_FORGE_MCP_ALLOW_NETWORK=1 is set for the MCP server."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch."},
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters of readable text to return.",
                    },
                },
                "required": ["url"],
            },
            handler=_web_fetch,
        ),
        MCPToolDefinition(
            name="web_search",
            description=(
                "Search for fresh external information. Default provider is offline. "
                "Set AGENT_FORGE_WEB_PROVIDER=duckduckgo|openai|claude and "
                "AGENT_FORGE_MCP_ALLOW_NETWORK=1 for live lookup."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum result snippets or citations to return.",
                    },
                },
                "required": ["query"],
            },
            handler=_web_search,
        ),
    ]


def _repo_policy(repo_root: Path, args: dict[str, Any]) -> MCPToolResult:
    """Return FORGE.md content or topic-filtered lines."""

    topic = str(args.get("topic") or "").strip().lower()
    policy_file = repo_root / "FORGE.md"
    if not policy_file.exists():
        return MCPToolResult.text(f"FORGE.md not found under {repo_root}", is_error=True)
    text = policy_file.read_text(encoding="utf-8")
    if not topic:
        return MCPToolResult.text(text[:5000])
    lines = [line for line in text.splitlines() if topic in line.lower()]
    body = "\n".join(lines[:40]) if lines else text[:1600]
    return MCPToolResult.text(body)


def _current_time() -> MCPToolResult:
    """Return time in a shape useful for trace/debug explanations."""

    local_now = datetime.now().astimezone().isoformat(timespec="seconds")
    utc_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return MCPToolResult.text(f"local_time: {local_now}\nutc_time: {utc_now}")


def _web_fetch(args: dict[str, Any]) -> MCPToolResult:
    """Fetch one page with network and scheme guards."""

    if not _network_enabled():
        return MCPToolResult.text(_network_disabled_message("web_fetch"), is_error=True)
    url = str(args.get("url") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return MCPToolResult.text("web_fetch requires an http or https URL", is_error=True)
    max_chars = _positive_int(args.get("max_chars"), default=4000, upper=12000)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AgentForgeMCP/0.1"})
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(max_chars * 6).decode(charset, errors="replace")
    except Exception as exc:
        return MCPToolResult.text(f"web_fetch failed: {exc}", is_error=True)
    text = _html_to_text(raw)[:max_chars]
    return MCPToolResult.text(f"url: {url}\n\n{text}")


def _web_search(args: dict[str, Any]) -> MCPToolResult:
    """Dispatch search to the configured provider."""

    query = str(args.get("query") or "").strip()
    if not query:
        return MCPToolResult.text("web_search requires query", is_error=True)
    provider = os.getenv("AGENT_FORGE_WEB_PROVIDER", "offline").strip().lower() or "offline"
    max_results = _positive_int(args.get("max_results"), default=5, upper=10)
    if provider == "offline":
        return MCPToolResult.text(
            "provider: offline\n"
            "network_call: false\n"
            f"query: {query}\n\n"
            "Live search is disabled for deterministic local runs. To enable it, run the MCP "
            "server with AGENT_FORGE_MCP_ALLOW_NETWORK=1 and set AGENT_FORGE_WEB_PROVIDER to "
            "duckduckgo, openai, or claude."
        )
    if not _network_enabled():
        return MCPToolResult.text(_network_disabled_message("web_search"), is_error=True)
    if provider == "duckduckgo":
        return _duckduckgo_search(query, max_results)
    if provider == "openai":
        return _openai_web_search(query)
    if provider == "claude":
        return _claude_web_search(query, max_results)
    return MCPToolResult.text(f"unsupported web provider: {provider}", is_error=True)


def _duckduckgo_search(query: str, max_results: int) -> MCPToolResult:
    """Use DuckDuckGo's lightweight HTML page as a no-key lookup provider."""

    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AgentForgeMCP/0.1"})
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            raw = response.read(120000).decode("utf-8", errors="replace")
    except Exception as exc:
        return MCPToolResult.text(f"duckduckgo search failed: {exc}", is_error=True)
    results = []
    for match in re.finditer(r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', raw, re.I | re.S):
        title = _html_to_text(match.group("title"))
        href = html.unescape(match.group("href"))
        if not title or "duckduckgo" in href:
            continue
        if href.startswith("//duckduckgo.com/l/?"):
            parsed = urllib.parse.urlparse("https:" + href)
            href = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
        results.append(f"- {title}\n  {href}")
        if len(results) >= max_results:
            break
    if not results:
        return MCPToolResult.text(f"provider: duckduckgo\nquery: {query}\n\nNo parseable results.")
    return MCPToolResult.text(f"provider: duckduckgo\nquery: {query}\n\n" + "\n".join(results))


def _openai_web_search(query: str) -> MCPToolResult:
    """Call OpenAI Responses API with the hosted web_search tool enabled."""

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AGENT_FORGE_OPENAI_API_KEY")
    if not api_key:
        return MCPToolResult.text("OPENAI_API_KEY is required for AGENT_FORGE_WEB_PROVIDER=openai", is_error=True)
    model = os.getenv("AGENT_FORGE_OPENAI_WEB_MODEL", "gpt-5.5")
    payload = {
        "model": model,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "input": query,
    }
    try:
        data = _post_json(
            "https://api.openai.com/v1/responses",
            payload,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    except Exception as exc:
        return MCPToolResult.text(f"openai web_search failed: {exc}", is_error=True)
    return MCPToolResult.text(f"provider: openai\nmodel: {model}\nquery: {query}\n\n{_extract_openai_text(data)}")


def _claude_web_search(query: str, max_results: int) -> MCPToolResult:
    """Call Anthropic Messages API with Claude's hosted web_search tool."""

    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    if not api_key:
        return MCPToolResult.text("ANTHROPIC_API_KEY is required for AGENT_FORGE_WEB_PROVIDER=claude", is_error=True)
    model = os.getenv("AGENT_FORGE_CLAUDE_WEB_MODEL", "claude-sonnet-4-5")
    payload = {
        "model": model,
        "max_tokens": 800,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_results}],
        "messages": [{"role": "user", "content": query}],
    }
    try:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            payload,
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
    except Exception as exc:
        return MCPToolResult.text(f"claude web_search failed: {exc}", is_error=True)
    return MCPToolResult.text(f"provider: claude\nmodel: {model}\nquery: {query}\n\n{_extract_claude_text(data)}")


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """POST JSON with stdlib only so MCP support adds no dependencies."""

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _extract_openai_text(data: dict[str, Any]) -> str:
    """Extract readable text from common Responses API shapes."""

    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts) or json.dumps(data, ensure_ascii=False, sort_keys=True)[:4000]


def _extract_claude_text(data: dict[str, Any]) -> str:
    """Extract text and URL citations from Claude Messages responses."""

    parts: list[str] = []
    citations: list[str] = []
    for block in data.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
        for citation in block.get("citations", []) or []:
            if isinstance(citation, dict) and citation.get("url"):
                title = citation.get("title") or citation.get("url")
                citations.append(f"- {title}: {citation.get('url')}")
    if citations:
        parts.append("citations:\n" + "\n".join(citations[:10]))
    return "\n\n".join(parts) or json.dumps(data, ensure_ascii=False, sort_keys=True)[:4000]


def _network_enabled() -> bool:
    """Return whether MCP web tools may perform outbound network calls."""

    return os.getenv("AGENT_FORGE_MCP_ALLOW_NETWORK", "").lower() in NETWORK_ENABLE_VALUES


def _network_disabled_message(tool_name: str) -> str:
    """Give a fixable error instead of silently trying the network."""

    return (
        f"{tool_name} network access is disabled. Set AGENT_FORGE_MCP_ALLOW_NETWORK=1 "
        "for the MCP server process when you intentionally want external lookup."
    )


def _timeout_seconds() -> float:
    """Bound external calls so the agent loop cannot hang on one provider."""

    try:
        return max(1.0, min(float(os.getenv("AGENT_FORGE_MCP_TIMEOUT", "8")), 30.0))
    except ValueError:
        return 8.0


def _positive_int(value: Any, *, default: int, upper: int) -> int:
    """Parse human/model-provided integer arguments defensively."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, upper))


def _html_to_text(raw: str) -> str:
    """Best-effort HTML to plain text conversion without adding dependencies."""

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

