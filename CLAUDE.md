# obsidian-mcp

MCP server exposing read/write tools over a **local Obsidian vault**, accessed
directly on disk — no Obsidian plugin, no Obsidian running. Obsidian picks up
changes live.

## Tech Stack

- Python ≥ 3.11, no runtime deps beyond `mcp>=1.2.0` (FastMCP)
- [uv](https://docs.astral.sh/uv/) for env + running — **never** `pip`/`python -m venv`
- pytest + pytest-asyncio (`asyncio_mode = "auto"`, so no `@pytest.mark.asyncio`)
- hatchling build, package at `src/obsidian_mcp`
- stdlib only for everything else: `urllib.request`, `base64`, `json`, `pathlib`

## Commands

```bash
uv sync                       # install deps
uv run pytest -q              # full suite (114 tests, ~0.5s — always run before committing)
uv run pytest tests/test_vault.py -q
OBSIDIAN_VAULT_PATH=/path/to/vault uv run obsidian-mcp             # stdio
OBSIDIAN_VAULT_PATH=/path/to/vault uv run obsidian-mcp --transport http --port 8757
bash scripts/chatgpt-tunnel.sh # macOS: vault picker + HTTP server + ngrok tunnel
bash -n scripts/chatgpt-tunnel.sh  # shell syntax check after editing the script
```

## Architecture

Three modules, one direction of dependency: `server.py` → `tools.py` → `vault.py`.

| File | Responsibility |
|---|---|
| `src/obsidian_mcp/vault.py` | `Vault` — all filesystem access and **all** path safety. Raises `VaultError`. |
| `src/obsidian_mcp/tools.py` | `register_tools(mcp, vault)` — MCP tool surface, argument decoding, network fetch, response formatting. |
| `src/obsidian_mcp/server.py` | `build_server()` + `main()` — CLI args (`--vault`/`$OBSIDIAN_VAULT_PATH`, `--transport`, `--port`), transport selection. |
| `scripts/chatgpt-tunnel.sh` | macOS launcher for the ChatGPT remote connector. |

**The load-bearing rule: path confinement lives in `Vault._resolve()` / `Vault._resolve_attachment()` and nowhere else.** Every tool goes through them. Do not
build paths from user input in `tools.py`, and do not add a second validation
layer there — a check in two places is a check that will drift.

Invariants to preserve when changing anything:

- No `../`, no absolute paths, nothing outside the vault root.
- Dot-folders (`.obsidian/`, `.trash/`) are invisible to listing and search.
- Notes are `.md` only (`require_md=True`); attachments are images only
  (`Vault.IMAGE_SUFFIXES`).
- Deletion is **always** soft — move into the vault's `.trash/`. Never `unlink`.
- Writes never clobber unless the caller passed `overwrite=True`.
- Note writes are atomic (`Vault._write_text_atomic`: temp file + rename) and
  capped at `MAX_NOTE_BYTES` (10 MB) — a failed write never truncates a note.
- Attachments go to Obsidian's configured `attachmentFolderPath`
  (`.obsidian/app.json`), falling back to `attachments/` — read the config,
  don't hardcode.

## Attachments and the network

`fetch_attachment` is the only tool that touches the network: the server
performs an outbound `GET` on whatever http(s) URL it is handed. It is bounded
by a suffix allowlist, a magic-byte check on the downloaded body
(`tools.py:_MAGIC_BYTES`), and a 25 MB cap. A prompt-injected instruction could
still aim that `GET` at a host reachable from this machine — that is a known,
documented trade-off, not an oversight. If you touch this code, keep all three
bounds and keep the README's safety paragraph in sync.

Network error handling has a subtlety worth remembering: `urllib` raises
`URLError` at *connection* time but plain `OSError` subclasses (e.g. socket
timeout) during *body reads*. Both must be wrapped in `VaultError` — see commit
`cbb7a25`.

## Conventions

- Tools return **human-readable strings**, not JSON — they are read by a model.
- Every failure path raises `VaultError` with a message that names the offending
  path/argument. No bare `except:`, no silently swallowed errors.
- Tests use the `vault` / `vault_dir` fixtures in `tests/conftest.py` (a real
  temp vault on disk, with `.obsidian/app.json`). Prefer extending that fixture
  over building ad-hoc directory trees.
- TDD: write the failing test first. `tests/test_vault.py` covers the safety
  invariants, `tests/test_tools.py` the MCP surface — a new tool needs both.
- New tool ⇒ update the README tool table in the same commit.
- `scripts/chatgpt-tunnel.sh` is `set -euo pipefail`; keep it POSIX-ish bash and
  re-run `bash -n` after editing.

## Docs

Design specs and implementation plans live in `docs/superpowers/`
(`specs/` for design, `plans/` for task breakdowns). Read the relevant spec
before changing a feature it covers.

## Gotchas

- **ngrok Host header** — the tunnel must pass `--host-header="localhost:$PORT"`
  or FastMCP's DNS-rebinding protection returns `421` on every tunneled request.
- **Tunnel won't come up** — usually another ngrok already holds port 4040, or a
  corporate proxy/firewall is blocking ngrok's endpoints. The script now prints
  the exact URLs ngrok failed to reach plus the log path; check that output
  before guessing.
- **The HTTP transport has no auth.** Anyone with the tunnel URL can read and
  write the vault. Never leave a tunnel running unattended, never commit a
  tunnel URL.
- MCP clients cache the tool list — **restart the client** (Claude Desktop,
  ChatGPT connector) after adding or renaming a tool, or it won't appear.
- Never commit a real vault path or vault contents.
