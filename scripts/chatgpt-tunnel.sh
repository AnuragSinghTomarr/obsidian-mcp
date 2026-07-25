#!/usr/bin/env bash
# ChatGPT connector launcher for obsidian-mcp (macOS).
# Asks for the Obsidian vault via a Finder folder picker, starts the MCP
# server over streamable HTTP, tunnels it with ngrok, and prints the
# connector URL to paste into ChatGPT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8757}"

command -v uv >/dev/null 2>&1 || { echo "error: uv is not installed — https://docs.astral.sh/uv/"; exit 1; }
command -v ngrok >/dev/null 2>&1 || { echo "error: ngrok is not installed — https://ngrok.com/download"; exit 1; }

VAULT="$(osascript -e 'POSIX path of (choose folder with prompt "Select your Obsidian vault")' 2>/dev/null)" \
  || { echo "error: no folder selected"; exit 1; }
echo "Vault: $VAULT"

cleanup() { kill "${SERVER_PID:-}" "${NGROK_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

OBSIDIAN_VAULT_PATH="$VAULT" uv --directory "$SCRIPT_DIR" run obsidian-mcp --transport http --port "$PORT" &
SERVER_PID=$!

# --host-header rewrites Host to what FastMCP's DNS-rebinding protection
# allows (localhost:*); without it every tunneled request gets 421.
ngrok http "$PORT" --host-header="localhost:${PORT}" --log stdout --log-format json >/dev/null &
NGROK_PID=$!

# ngrok exposes the tunnel URL on its local API once the tunnel is up
URL=""
for _ in $(seq 1 30); do
  sleep 0.5
  URL="$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c '
import json, sys
try:
    tunnels = json.load(sys.stdin)["tunnels"]
    print(tunnels[0]["public_url"] if tunnels else "")
except Exception:
    pass' 2>/dev/null)"
  [ -n "$URL" ] && break
done
[ -n "$URL" ] || { echo "error: ngrok tunnel did not come up (is another ngrok already running?)"; exit 1; }
kill -0 "$SERVER_PID" 2>/dev/null || { echo "error: obsidian-mcp server failed to start"; exit 1; }

echo
echo "==============================================================="
echo "  ChatGPT connector URL:  ${URL}/mcp"
echo "==============================================================="
echo "  Add it in ChatGPT: Settings -> Connectors -> Advanced ->"
echo "  Developer mode -> New connector -> paste the URL above."
echo
echo "  WARNING: no auth — anyone with this URL can read/write the vault."
echo "  Press Ctrl-C to stop the server and close the tunnel."
echo
wait
