# MCP tool annotations on all tools

**Date:** 2026-08-04 · **Status:** Approved · **Branch:** feat/vault-attachments

## Goal

Declare MCP `ToolAnnotations` (readOnlyHint / destructiveHint / idempotentHint
/ openWorldHint) on every tool so safety layers in MCP clients — notably
ChatGPT's connector guardrail, which blocked a call with "couldn't determine
the safety status" — can classify calls instead of guessing. No behavior
changes.

Key motivation: `openWorldHint` defaults to **true** in the MCP spec, so
today all ten tools look like they might reach the outside world. Nine are
purely local.

## The matrix (approved)

| Tool | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|
| `list_notes`, `read_note`, `search_notes` | true | — | — | false |
| `append_note` | false | false (additive) | false | false |
| `move_note` | false | false (non-lossy rename, refuses clobber) | false | false |
| `write_note` | false | true (can overwrite) | false | false |
| `replace_in_note` | false | true (edits content) | false | false |
| `delete_note` | false | true (soft, but should warn) | false | false |
| `write_attachment` | false | true (can overwrite) | false | false |
| `fetch_attachment` | false | true | false | **true** (only network tool) |

Decisions: `delete_note` is destructive despite soft-delete (clients should
warn); `move_note` is non-destructive (refuses to clobber). No `title`
annotations (YAGNI). Annotations are shared module-level `ToolAnnotations`
constants in `tools.py`, one per profile, passed as
`@mcp.tool(annotations=…)` — mcp 1.28.1 supports this and exposes them via
`list_tools`.

## Tests / docs

One test in `tests/test_tools.py` asserting the full matrix via
`server.list_tools()`. One sentence in the README safety paragraph. A vault
restart note is unnecessary (README already covers client caching).
