# Obsidian MCP Server — Design Spec

**Date:** 2026-07-24
**Status:** Approved (brainstorming session)

## Goal

An MCP server that lets AI clients (Claude Desktop / Claude Code via stdio, ChatGPT via
remote connector) read and write a local Obsidian vault. The vault is accessed directly
on the filesystem — no Obsidian plugin required, and it works whether or not the
Obsidian app is running (Obsidian picks up disk changes live).

## Stack

- Python ≥ 3.11, environment and packaging managed with **uv** (`pyproject.toml`, `uv.lock`).
- Official **`mcp` Python SDK**, using the `FastMCP` server API.
- No other runtime dependencies unless forced.

## Configuration

- Vault location: `OBSIDIAN_VAULT_PATH` env var, overridable by `--vault <path>` CLI arg.
  The server fails fast at startup with a clear error if the path is missing or not a
  directory.
- Transport: `--transport stdio` (default) or `--transport http`.
  - `stdio` — for Claude Desktop / Claude Code (`claude mcp add … -- uv run obsidian-mcp`).
  - `http` — streamable HTTP on `127.0.0.1:<port>` (`--port`, default 8757) for ChatGPT
    developer-mode connectors. ChatGPT cannot spawn local processes, so reaching it from
    ChatGPT requires exposing the HTTP endpoint via a tunnel (ngrok / cloudflared);
    that tunnel setup is documented in the README but out of scope for the server code.

## Safety rails (apply to every tool)

1. **Path confinement** — every path argument is joined to the vault root and resolved
   (`Path.resolve()`); if the result is not inside the vault root, the tool returns an
   error. This blocks `../` traversal and absolute paths.
2. **Hidden internals** — `.obsidian/`, `.trash/`, and any dot-prefixed file/folder are
   excluded from listings and search, and cannot be read or written through the tools.
3. **Soft delete** — `delete_note` moves the file into `.trash/` at the vault root
   (Obsidian's own trash convention), never a hard delete. Name collisions in `.trash/`
   are resolved by suffixing a counter (`note.md`, `note 1.md`, …).
4. **Markdown scope** — tools operate on `.md` files. `list_notes` shows folders and
   notes; non-markdown files (images, PDFs) are ignored in v1.

## Tools

| Tool | Signature (vault-relative paths) | Behavior |
|---|---|---|
| `list_notes` | `(folder: str = "", recursive: bool = true)` | List notes (and subfolders) under `folder`. Returns relative paths. |
| `read_note` | `(path: str)` | Return full note content. Error if missing. |
| `write_note` | `(path: str, content: str, overwrite: bool = false)` | Create note, auto-creating parent folders. If the file exists and `overwrite` is false → error; `overwrite=true` replaces content. |
| `append_note` | `(path: str, content: str)` | Append to an existing note (ensures a newline separator). Error if missing. |
| `search_notes` | `(query: str, folder: str = "")` | Case-insensitive substring search across note contents (and filenames). Returns matches as `path` + line number + surrounding context (±1 line), capped (e.g. 50 matches) to keep responses bounded. |
| `move_note` | `(source: str, destination: str)` | Rename/move within the vault; auto-creates destination folders; error if destination exists. |
| `delete_note` | `(path: str)` | Soft-delete: move to `.trash/`. |

Tool errors are returned as MCP tool errors with human-readable messages (e.g.
"Note not found: foo/bar.md"), never stack traces.

## Explicitly out of scope (v1)

- Frontmatter/tag-specific tools — frontmatter is plain text; clients edit it via
  `read_note` + `write_note`.
- Backlink/graph queries, daily-note helpers, attachment handling, Obsidian URI
  integration, auth on the HTTP transport (tunnel provides the private link).

## Project layout

```
obsidian-mcp/
├── pyproject.toml          # uv-managed; console script `obsidian-mcp`
├── uv.lock
├── README.md               # setup for Claude Desktop/Code + ChatGPT connector
├── src/obsidian_mcp/
│   ├── __init__.py
│   ├── server.py           # FastMCP app, CLI (argparse), transport selection
│   ├── vault.py            # Vault class: path resolution, all file operations
│   └── tools.py            # MCP tool definitions delegating to Vault
└── tests/
    ├── conftest.py         # tmp vault fixture with sample notes
    ├── test_vault.py       # Vault unit tests incl. guardrails
    └── test_tools.py       # tool-level tests via MCP in-memory client
```

`vault.py` holds all filesystem logic and is fully testable without MCP; `tools.py` is a
thin layer mapping tool calls to `Vault` methods and formatting errors.

## Testing

pytest against a temp-directory vault fixture:

- Happy path per tool (list/read/write/append/search/move/delete).
- Guardrails: `../` traversal rejected, absolute path rejected, `.obsidian` invisible
  and unwritable, delete lands in `.trash/` with collision suffixing.
- Edge cases: write with `overwrite` both ways, append to missing note, search cap.
