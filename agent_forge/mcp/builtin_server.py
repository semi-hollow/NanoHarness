from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_forge.mcp.server import AgentForgeMCPServer
from agent_forge.mcp.web_tools import build_builtin_tools


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(description="Run the NanoHarness built-in MCP stdio server.")
    parser.add_argument("--list-tools", action="store_true", help="Print available tool schemas and exit.")
    parser.add_argument("--call", help="Call one tool directly for debugging instead of starting stdio mode.")
    parser.add_argument("--args-json", default="{}", help="JSON object passed to --call.")
    parser.add_argument("--workspace", default=".", help="Workspace root used by repo_policy.")
    return parser


def main() -> None:

    args = build_parser().parse_args()
    tools = build_builtin_tools(Path(args.workspace).resolve())
    server = AgentForgeMCPServer(tools)
    if args.list_tools:
        print(json.dumps({"tools": [tool.to_mcp_schema() for tool in tools]}, ensure_ascii=False, indent=2))
        return
    if args.call:
        raw_args = json.loads(args.args_json)
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": args.call, "arguments": raw_args},
            }
        )
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    server.run()

if __name__ == "__main__":
    main()
