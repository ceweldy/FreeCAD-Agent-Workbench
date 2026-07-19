# AgentCAD 0.1 behavior contract

1. A user can install AgentCAD as a FreeCAD addon and open a native dock panel
   without changing FreeCAD's source or theme.
2. The panel reports whether Codex is installed and authenticated, offers a
   native ChatGPT connection flow, and never reveals credentials.
3. A saved FreeCAD document can start a persistent conversation. An unsaved
   document receives a clear save-first message.
4. A Codex turn can stream text, call only registered AgentCAD tools, and be
   cancelled.
5. Safe CAD writes produce structured receipts and roll back when execution or
   verification fails.
6. Conversations and durable memory survive an AgentCAD restart and remain
   scoped to the correct CAD document.
7. Invalid tool arguments, unknown tools, destructive tools, shell requests,
   file-change requests, and permission-expansion requests fail closed.
8. Runtime diagnostics work without FreeCAD and never print credential values.
