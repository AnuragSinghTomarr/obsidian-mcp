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

NGROK_LOG="$(mktemp -t obsidian-mcp-ngrok)"
KEEP_NGROK_LOG=0
cleanup() {
  kill "${SERVER_PID:-}" "${NGROK_PID:-}" 2>/dev/null || true
  [ "$KEEP_NGROK_LOG" = 1 ] || rm -f "$NGROK_LOG"
}
trap cleanup EXIT INT TERM

# A firewall block shows up in ngrok's log as a dial/lookup failure naming the
# endpoint it could not reach. Print those hosts instead of just "tunnel did
# not come up", so the URL to allowlist is visible in the terminal.
report_ngrok_failure() {
  KEEP_NGROK_LOG=1
  python3 - "$NGROK_LOG" <<'PY'
import html, json, re, sys

URL_RE = re.compile(r'https?://[^\s"\\<>]+')
# bare host:port / ip:port, for dial and DNS-lookup failures that carry no URL
HOST_RE = re.compile(
    r'\b(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?::\d+)?', re.I
)
TAG_RE = re.compile(r'<[^>]*>')


def condense(text):
    """A proxy's block page can arrive as a whole HTML document — keep one line."""
    text = html.unescape(" ".join(TAG_RE.sub(" ", text).split()))
    return text if len(text) <= 200 else text[:200] + " ..."


try:
    lines = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
except OSError as exc:
    print(f"  (could not read ngrok log: {exc})")
    raise SystemExit(0)

endpoints, problems = [], []
for line in lines:
    try:
        record = json.loads(line)
    except ValueError:
        continue
    if record.get("lvl") not in ("warn", "eror", "crit"):
        continue
    detail = " ".join(str(record[k]) for k in ("msg", "err") if record.get(k))
    summary = condense(detail)
    if summary and summary not in problems:
        problems.append(summary)
    urls = [u.rstrip('.,;"') for u in URL_RE.findall(detail)]
    # a doctype's DTD reference is markup boilerplate, not an endpoint ngrok dialled
    urls = [u for u in urls if not u.lower().endswith((".dtd", ".xsd"))]
    # bare-host matching would read an embedded stylesheet's `td.bh` as a host
    is_html = "<html" in detail.lower() or "<!doctype" in detail.lower()
    hosts = [] if is_html else [
        h for h in HOST_RE.findall(detail) if not any(h in u for u in urls)
    ]
    for endpoint in [record.get("url", "")] + urls + hosts:
        if endpoint and endpoint not in endpoints:
            endpoints.append(endpoint)

if endpoints:
    print("  URLs ngrok could not reach (allowlist these in your firewall):")
    for endpoint in endpoints[:10]:
        print(f"    {endpoint}")
    if len(endpoints) > 10:
        print(f"    ... and {len(endpoints) - 10} more, see the log below")
if problems:
    print("  ngrok reported:")
    for problem in problems[:10]:
        print(f"    {problem}")
if not endpoints and not problems:
    print("  ngrok logged no errors; last lines of its log:")
    for line in lines[-5:]:
        print(f"    {condense(line)}")
PY
  echo "  full ngrok log: $NGROK_LOG"
}

OBSIDIAN_VAULT_PATH="$VAULT" uv --directory "$SCRIPT_DIR" run obsidian-mcp --transport http --port "$PORT" &
SERVER_PID=$!

# --host-header rewrites Host to what FastMCP's DNS-rebinding protection
# allows (localhost:*); without it every tunneled request gets 421.
ngrok http "$PORT" --host-header="localhost:${PORT}" --log stdout --log-format json >"$NGROK_LOG" 2>&1 &
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
[ -n "$URL" ] || {
  echo "error: ngrok tunnel did not come up (is another ngrok already running, or a firewall blocking it?)"
  report_ngrok_failure
  exit 1
}
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
