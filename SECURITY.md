# Security policy

Agent Workbench for FreeCAD controls a CAD application through model-selected
tool calls. Treat its tool registry and provider boundary as security-sensitive
code.

## Supported versions

Security fixes are applied to the latest release and the current `main` branch.

## Reporting

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security-advisory workflow for this repository.

## Credential handling

The Codex provider delegates ChatGPT sign-in, credential persistence, and token
refresh to the official Codex app-server. Agent Workbench does not read or copy
the underlying OAuth token. Future API providers must use the operating-system
credential store or an explicitly selected secret source, never ordinary
FreeCAD preferences or project memory.

The app-server receives a stable, private workbench-managed Codex home. Codex's
managed browser flow authenticates that dedicated home once. Agent Workbench forces
Codex's file-backed credential store into the owner-only profile so no keyring
identity can overlap another Codex installation. User configuration, MCP
servers, plugins, skills, and rules are not imported. The persistent home keeps
Codex provider thread state available after the app-server restarts.

When a user starts a turn, Agent Workbench sends the selected provider the prompt,
recent conversation turns, structured project memory, and a compact summary of
the active CAD document. The summary omits the document's absolute local path.
Users should still treat prompts, object names, dimensions, and memory as design
data shared with that provider.

## Threat model

- Provider output is untrusted until it passes a tool schema and policy check.
- CAD mutations run only through named tools on FreeCAD's GUI thread.
- Failed mutations roll back.
- Codex child commands inherit a minimal environment and a host-enforced
  permission profile. The profile permits only minimal runtime reads and the
  isolated workbench runtime directory, with sandboxed network access disabled.
- A Codex turn is terminated if it attempts shell, arbitrary Python, file
  changes, web search, non-workbench tools, or permission expansion.
- Project conversations and memory may contain sensitive design information.
  They are stored locally with owner-only permissions and must not be committed
  automatically.
