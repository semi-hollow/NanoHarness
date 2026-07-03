# Security Policy

Agent Forge is a local CodingAgent runtime core. It can read files, write files,
apply patches, and run approved commands inside a workspace, so security
boundaries matter even in local development.

## Supported Security Boundary

The repository currently provides:

- Workspace path checks through `WorkspaceSandbox`.
- Command allowlists and high-risk command blocking through `CommandPolicy`.
- Approval modes through runtime hooks.
- Role-level tool allowlists in the multi-agent coordinator.
- Artifact handoff for multi-agent communication instead of hidden peer chat.
- Optional worktree execution for isolating code changes from the main checkout.
- Network-deny behavior for local command execution unless explicitly enabled.
- Offline-by-default MCP web tools.

This is not a container sandbox. Do not treat it as equivalent to Docker,
Firecracker, gVisor, or a remote isolated execution service.

## Reporting A Vulnerability

For public repositories, open a private security advisory when available. If
private advisories are not enabled, contact the repository owner directly before
publishing exploit details.

Please include:

- Affected file or module.
- Steps to reproduce.
- Expected and actual behavior.
- Whether the issue can read secrets, execute unexpected commands, escape the
  workspace, or bypass approval.

## Secret Handling

Never commit real provider keys. Use shell environment variables or ignored
local files for:

- `DEEPSEEK_API_KEY`
- `OPENAI_API_KEY`
- `AGENT_FORGE_API_KEY`

The repository intentionally ignores `.env`, `.env.local`, `llm_profiles.json`,
and `.agent_forge/`.

Agent Forge does not implement Claude / Anthropic provider compatibility in this
version. Do not add Anthropic credentials unless a future provider module is
explicitly implemented and reviewed.

## Disclosure Scope

Security issues in external model providers, operating systems, shells, or
package managers are outside this repository unless Agent Forge mishandles their
responses or bypasses its own policies.
