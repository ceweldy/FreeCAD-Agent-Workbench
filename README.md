# AgentCAD

AgentCAD is an agent-native modeling environment delivered as a FreeCAD addon.
It combines FreeCAD's parametric CAD foundation with a native assistant panel,
bounded CAD tools, undo-safe transactions, durable project memory, and a
provider-neutral agent runtime.

The first provider is OpenAI Codex. AgentCAD launches the official Codex
app-server locally and uses Codex's managed ChatGPT sign-in inside a dedicated
AgentCAD profile. AgentCAD never reads or copies ChatGPT OAuth credentials. Its
stable Codex home preserves provider thread state without loading the user's MCP
servers, plugins, skills, rules, or general Codex configuration.

## Current capabilities

- Native FreeCAD dock panel that follows the active application palette
- Persistent project conversations and structured design memory
- Codex app-server connection with streaming responses and cancellation
- Versioned JSON tool contracts filtered by safety policy
- Automatic rollback when a CAD mutation or postcondition fails
- Compact document, selection, workbench, and geometry context
- Working Part primitives, boolean operations, measurement, view control, and
  memory tools
- Provider interface designed for future OpenAI API, Anthropic, and local-model
  adapters
- Dependency-free runtime, with development dependencies isolated from FreeCAD

## Requirements

- FreeCAD 1.1 or newer
- Python 3.11 or newer, provided by FreeCAD
- Codex CLI 0.138.0 or newer for the initial provider

Install Codex, open the AgentCAD panel, and choose **Connect**. Codex opens the
official ChatGPT browser sign-in and then owns storage and automatic token
refresh inside AgentCAD's private profile. You can check the general Codex CLI
installation separately with:

```bash
codex --version
```

API keys are not required. AgentCAD forces Codex's file-backed credential store
inside its private, owner-only profile so the connection cannot overlap another
Codex installation's keyring identity. Credential values are never exposed to
AgentCAD.

## Developer installation

Clone this repository into FreeCAD's user `Mod` directory as `AgentCAD`, then
restart FreeCAD. Common locations are:

- macOS: `~/Library/Application Support/FreeCAD/Mod/AgentCAD`
- Linux: `~/.local/share/FreeCAD/Mod/AgentCAD`
- Windows: `%APPDATA%\FreeCAD\Mod\AgentCAD`

For local development, a symbolic link to the checkout works as well.

Open the panel using **View > Panels > AgentCAD** or the AgentCAD toolbar
button. Save the active FreeCAD document before starting an agent conversation
so its memory has a durable project identity.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
./scripts/agentcad-doctor --json
```

The runtime has no third-party Python dependency. Tests use lightweight FreeCAD
fakes so the core can be verified in CI without embedding the FreeCAD desktop.

## Safety model

AgentCAD does not expose arbitrary Python or shell execution as a CAD tool.
Each callable operation has a schema, safety level, edit-state contract, and
postcondition. Mutations run inside a FreeCAD transaction and roll back on
failure. Destructive, external, and developer capabilities are denied by the
default provider policy.

Codex runs with an AgentCAD-defined permission profile. Host sandbox rules allow
only minimal operating-system runtime reads plus read access to an isolated,
empty AgentCAD runtime directory. Other filesystem reads, writes, and sandboxed
network access are denied. AgentCAD uses a stable private Codex home for login
and thread state, but overwrites its managed configuration and does not import
the user's Codex configuration, MCP servers, plugins, skills, or rules.
AgentCAD terminates a turn that attempts a built-in command, file
change, web search, or non-AgentCAD tool, and it rejects requests to expand
permissions.

See [Architecture](docs/ARCHITECTURE.md), [Security](SECURITY.md), and the
[Roadmap](docs/ROADMAP.md) for more detail.

## Open-source direction

The project is MIT licensed and structured for publication through FreeCAD's
addon ecosystem. FreeCAD remains an independent LGPL-licensed dependency and
is not copied into this repository.
