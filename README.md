# obsidian-mcp

MCP server exposing read/write tools over a local Obsidian vault. The vault is
accessed directly on disk — no Obsidian plugin needed, and Obsidian picks up
changes live.

## Setup

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this-repo> && cd obsidian-mcp
uv sync
```

Point it at your vault via `OBSIDIAN_VAULT_PATH` or `--vault`:

```bash
OBSIDIAN_VAULT_PATH="$HOME/Documents/MyVault" uv run obsidian-mcp
```

## Tools

| Tool | Description |
|---|---|
| `list_notes(folder="", recursive=true)` | List notes and folders |
| `read_note(path)` | Read a note's full content |
| `write_note(path, content, overwrite=false)` | Create (or overwrite) a note |
| `append_note(path, content)` | Append to an existing note |
| `search_notes(query, folder="")` | Case-insensitive search (≤50 matches, ±1 line context) |
| `move_note(source, destination)` | Move/rename a note |
| `delete_note(path)` | Soft-delete to the vault's `.trash/` |
| `write_attachment(filename, base64_data, folder="", overwrite=false)` | Save a base64 image; returns its path and `![[embed]]` |
| `fetch_attachment(filename, url, folder="", overwrite=false)` | Download an image from an http(s) URL into the vault |

Safety: all paths are confined to the vault (no `../`, no absolute paths),
dot-folders like `.obsidian/` are invisible, and deletion is always soft.

Attachments: images only (`.png .jpg .jpeg .gif .webp .svg .bmp`), capped at
25 MB, written to the folder Obsidian is configured to use
(`attachmentFolderPath` in `.obsidian/app.json`, defaulting to `attachments/`).
`fetch_attachment` makes the server perform an outbound `GET` on whatever
http(s) URL it is given — the only tool here that touches the network. Downloads
must match their claimed image type, so an error page is rejected rather than
saved, but note that a prompt-injected instruction could still aim that `GET` at
a host reachable from this machine.

## Claude Code

```bash
claude mcp add obsidian -e OBSIDIAN_VAULT_PATH="$HOME/Documents/MyVault" \
  -- uv --directory /path/to/obsidian-mcp run obsidian-mcp
```

## Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "uv",
      "args": ["--directory", "/path/to/obsidian-mcp", "run", "obsidian-mcp"],
      "env": { "OBSIDIAN_VAULT_PATH": "/Users/you/Documents/MyVault" }
    }
  }
}
```

## ChatGPT (remote connector)

**macOS one-liner:** `./scripts/chatgpt-tunnel.sh` — picks the vault via a
Finder dialog, starts the HTTP server, tunnels it with ngrok, and prints the
connector URL. Or do it manually:

ChatGPT only connects to MCP servers over HTTP, so run the HTTP transport and
expose it with a tunnel:

```bash
OBSIDIAN_VAULT_PATH="$HOME/Documents/MyVault" uv run obsidian-mcp --transport http --port 8757
# in another terminal:
ngrok http 8757        # or: cloudflared tunnel --url http://localhost:8757
```

Then in ChatGPT: Settings → Connectors → Advanced → Developer mode → add the
tunnel URL with path `/mcp` (e.g. `https://<id>.ngrok.app/mcp`).

⚠️ The HTTP transport has no auth in v1 — anyone with the tunnel URL can read
and write your vault. Only run the tunnel while you're using it.

## Development

```bash
uv run pytest
```
