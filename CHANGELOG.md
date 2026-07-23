# Changelog

## Unreleased

- Find Codex in standard desktop installation paths when FreeCAD is launched
  outside a shell.
- Add an optional familiar native workspace with familiar command names,
  grouped modeling toolbars, and matching pan and orbit controls.
- Expand the familiar workspace with a Design selector, focused command
  tabs, a left Browser, a selectable bottom Timeline, a compact bottom
  navigation bar, and a right Agent Workbench assistant.
- Save every successful CAD write to the active document plus a private rolling
  checkpoint, and recover interrupted conversations after a FreeCAD restart.
- Avoid repeated Fusion-workspace widget rearrangement after the native layout
  has already been applied.

All notable Agent Workbench for FreeCAD changes are recorded here.

## 0.1.0, 2026-07-19

- Added a native FreeCAD workbench and docked assistant interface.
- Added persistent document-scoped conversations and structured intent memory.
- Added the OpenAI Codex app-server provider with live streaming and cancellation.
- Added versioned, policy-filtered CAD tools with rollback-first transactions.
- Added Part primitives, booleans, measurements, view control, and memory tools.
- Added diagnostics, a live Codex smoke test, CI, security guidance, and tests.
