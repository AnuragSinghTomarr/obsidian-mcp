from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from obsidian_mcp.vault import Vault


def register_tools(mcp: FastMCP, vault: Vault) -> None:
    @mcp.tool()
    def list_notes(folder: str = "", recursive: bool = True) -> str:
        """List notes and folders in the vault (vault-relative paths).

        Args:
            folder: Subfolder to list; empty string means the vault root.
            recursive: Include all nested notes and folders.
        """
        return json.dumps(vault.list_notes(folder, recursive), indent=2)

    @mcp.tool()
    def read_note(path: str) -> str:
        """Read the full content of a note.

        Args:
            path: Vault-relative path ending in .md, e.g. "Projects/Idea.md".
        """
        return vault.read_note(path)

    @mcp.tool()
    def write_note(path: str, content: str, overwrite: bool = False) -> str:
        """Create a note (parent folders auto-created).

        Args:
            path: Vault-relative path ending in .md.
            content: Full markdown content.
            overwrite: Must be true to replace an existing note.
        """
        vault.write_note(path, content, overwrite)
        return f"Wrote {path}"

    @mcp.tool()
    def append_note(path: str, content: str) -> str:
        """Append text to an existing note (a newline separator is ensured).

        Args:
            path: Vault-relative path of an existing note.
            content: Text to append.
        """
        vault.append_note(path, content)
        return f"Appended to {path}"

    @mcp.tool()
    def search_notes(query: str, folder: str = "") -> str:
        """Case-insensitive text search across note contents and filenames.

        Returns up to 50 matches as JSON: path, 1-based line (0 = filename
        match), and the matching line with one line of context either side.

        Args:
            query: Substring to search for.
            folder: Restrict the search to this subfolder.
        """
        return json.dumps(vault.search_notes(query, folder), indent=2)

    @mcp.tool()
    def move_note(source: str, destination: str) -> str:
        """Move or rename a note (destination folders auto-created).

        Args:
            source: Existing vault-relative note path.
            destination: New vault-relative note path; must not exist.
        """
        vault.move_note(source, destination)
        return f"Moved {source} -> {destination}"

    @mcp.tool()
    def delete_note(path: str) -> str:
        """Soft-delete a note by moving it to the vault's .trash/ folder.

        Args:
            path: Vault-relative path of the note to delete.
        """
        trashed = vault.delete_note(path)
        return f"Moved to {trashed}"
