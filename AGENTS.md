# Agent Workbench for FreeCAD contributor instructions

## Product boundaries

- Keep Agent Workbench installable as a normal FreeCAD addon. Do not patch FreeCAD
  core unless a separate proposal demonstrates that the public addon APIs are
  insufficient.
- Keep provider code separate from CAD tools, project storage, and Qt UI.
- Keep the runtime dependency-free unless a dependency removes substantially
  more risk than it adds.
- Never expose arbitrary Python, shell, or file-system execution as a normal
  provider-callable CAD tool.

## CAD safety

- All mutations must run through `FreeCADTransaction`.
- Validate arguments before touching the document.
- Add a concrete postcondition for every mutation.
- Roll back failed mutations by default.
- Treat delete, export, external-process, and developer tools as approval-gated
  or unavailable to providers.
- Run FreeCAD mutations on the GUI thread.

## Compatibility

- Support FreeCAD 1.1 and Python 3.11 or newer.
- Use the `PySide` compatibility namespace provided by FreeCAD.
- Do not hard-code colors or fonts. Follow the active FreeCAD Qt palette.
- Preserve existing tool names, schemas, memory formats, and preference keys.
  Version contracts before making incompatible changes.

## Verification

- Run `python -m pytest` and `python -m ruff check .` for every code change.
- Run `./scripts/agent-workbench-doctor --json` for provider or packaging changes.
- Run AutoReview after non-trivial changes and before publishing.
- Pair source review with behavior validation for UI, CLI, or operator-facing
  behavior.

## Secrets

- Never commit credentials, tokens, session files, `.env` files, private CAD
  documents, screenshots, or conversation stores.
- Agent Workbench may invoke Codex authentication commands, but it must never parse or
  persist the credential payload itself.
