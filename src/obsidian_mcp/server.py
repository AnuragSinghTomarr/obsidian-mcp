from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from obsidian_mcp.tools import register_tools
from obsidian_mcp.vault import Vault, VaultError


def build_server(vault_path: Path, host: str = "127.0.0.1", port: int = 8757) -> FastMCP:
    vault = Vault(vault_path)
    mcp = FastMCP("obsidian-mcp", host=host, port=port)
    register_tools(mcp, vault)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="obsidian-mcp",
        description="MCP server for a local Obsidian vault",
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get("OBSIDIAN_VAULT_PATH"),
        help="Path to the Obsidian vault (default: $OBSIDIAN_VAULT_PATH)",
    )
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=8757, help="Port for --transport http")
    args = parser.parse_args()

    if not args.vault:
        sys.exit("error: set OBSIDIAN_VAULT_PATH or pass --vault <path>")
    try:
        mcp = build_server(Path(args.vault), port=args.port)
    except VaultError as exc:
        sys.exit(f"error: {exc}")

    mcp.run(transport="streamable-http" if args.transport == "http" else "stdio")


if __name__ == "__main__":
    main()
