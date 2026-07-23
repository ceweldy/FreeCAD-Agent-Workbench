# Agent Workbench for FreeCAD

Agent Workbench for FreeCAD is an open, agent-native modeling environment
delivered as a normal FreeCAD addon. It combines FreeCAD's parametric CAD
foundation with a native assistant panel, bounded CAD tools, undo-safe
transactions, durable project memory, and a provider-neutral agent runtime.

This is an independent community project. It is not affiliated with or endorsed
by the FreeCAD Project Association, Autodesk, or OpenAI. See
[Third-party notices](THIRD_PARTY_NOTICES.md).

The first provider is OpenAI Codex. Agent Workbench launches the official Codex
app-server locally and uses Codex's managed ChatGPT sign-in inside a dedicated
private profile. Agent Workbench never reads or copies ChatGPT OAuth
credentials. Its stable Codex home preserves provider thread state without
loading the user's MCP servers, plugins, skills, rules, or general Codex
configuration.

## Current capabilities

- Native FreeCAD dock panel that follows the active application palette
- Persistent project conversations and structured design memory
- Codex app-server connection with streaming responses and cancellation
- Versioned JSON tool contracts filtered by safety policy
- Automatic rollback when a CAD mutation or postcondition fails
- Automatic document saves and private crash-safe checkpoints after every
  successful CAD write
- Interrupted-task recovery in the conversation after FreeCAD restarts
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

Install Codex, open the Agent Workbench panel, and choose **Connect**. Codex opens the
official ChatGPT browser sign-in and then owns storage and automatic token
refresh inside the workbench's private profile. You can check the general Codex CLI
installation separately with:

```bash
codex --version
```

API keys are not required. Agent Workbench forces Codex's file-backed credential store
inside its private, owner-only profile so the connection cannot overlap another
Codex installation's keyring identity. Credential values are never exposed to
the workbench.

## Developer installation

Clone this repository into FreeCAD's user `Mod` directory as `AgentWorkbench`, then
restart FreeCAD. Common locations are:

- macOS: `~/Library/Application Support/FreeCAD/Mod/AgentWorkbench`
- Linux: `~/.local/share/FreeCAD/Mod/AgentWorkbench`
- Windows: `%APPDATA%\FreeCAD\Mod\AgentWorkbench`

For local development, a symbolic link to the checkout works as well.

Open the panel using **View > Panels > Agent Workbench** or the workbench toolbar
button. Save the active FreeCAD document before starting an agent conversation
so its memory has a durable project identity.

In **Agent Workbench Settings**, enable **Use familiar Design workspace and
navigation** for a modeling layout organized for users coming from Autodesk
Fusion. The native workspace adds a Design selector with Solid, Construct,
Inspect, and Agent tabs. Each tab exposes focused FreeCAD command groups with
familiar names such as Extrude, Combine, and Shell. The model tree becomes the
Browser on the left, Agent Workbench stays on the right, design history appears
in a selectable Timeline along the bottom, and a compact bottom navigation bar
provides fit and standard views. Middle-mouse pans, Shift-middle-mouse orbits,
zoom follows the cursor, and the navigation cube stays visible.

The optional workspace follows the active FreeCAD palette and stores the
previous window layout before it makes any changes. Turning the setting off
restores the prior layout and navigation preferences. No FreeCAD core files are
patched.

Agent Workbench keeps the latest 20 crash-safe document checkpoints inside the
active document's private project directory. A successful write is
checkpointed and saved before the tool result returns to Codex. If FreeCAD
stops before an assistant response is recorded, the next launch labels the task
as interrupted and identifies any matching checkpoint in the conversation.

Existing installations retain the internal `AgentCAD` preference, storage,
schema, command, and tool identifiers. These stable compatibility identifiers
keep conversations, memory, Codex authentication, and saved layouts working
through the public rename.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
./scripts/agent-workbench-doctor --json
```

The runtime has no third-party Python dependency. Tests use lightweight FreeCAD
fakes so the core can be verified in CI without embedding the FreeCAD desktop.

## Safety model

Agent Workbench does not expose arbitrary Python or shell execution as a CAD tool.
Each callable operation has a schema, safety level, edit-state contract, and
postcondition. Mutations run inside a FreeCAD transaction and roll back on
failure. Destructive, external, and developer capabilities are denied by the
default provider policy.

Codex runs with a workbench-defined permission profile. Host sandbox rules allow
only minimal operating-system runtime reads plus read access to an isolated,
empty runtime directory. Other filesystem reads, writes, and sandboxed network
access are denied. Agent Workbench uses a stable private Codex home for login
and thread state, but overwrites its managed configuration and does not import
the user's Codex configuration, MCP servers, plugins, skills, or rules.
Agent Workbench terminates a turn that attempts a built-in command, file
change, web search, or non-AgentCAD tool, and it rejects requests to expand
permissions.

See [Architecture](docs/ARCHITECTURE.md), [Security](SECURITY.md), and the
[Roadmap](docs/ROADMAP.md) for more detail.

## License and project status

The project is community-maintained, free to use, and licensed under MIT. It is
structured for publication through FreeCAD's addon ecosystem. FreeCAD remains
an independent LGPL-licensed dependency and is not copied into this repository.
Contributions are welcome under the same MIT license.
