from __future__ import annotations

import base64
import binascii
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from obsidian_mcp.vault import Vault, VaultError


def _attachment_result(saved: str, size: int) -> str:
    """JSON for a saved attachment: its path, a ready-to-paste embed, its size."""
    return json.dumps(
        {"path": saved, "embed": f"![[{Path(saved).name}]]", "bytes": size},
        indent=2,
    )


_MAGIC_BYTES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
}


def _check_magic(filename: str, data: bytes) -> None:
    """Reject a download whose bytes don't match its extension.

    Stops an HTML error page from being saved as a .png. SVG is skipped
    because it is text with no fixed signature.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".svg":
        return
    if suffix == ".webp":
        ok = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    else:
        ok = any(data.startswith(sig) for sig in _MAGIC_BYTES.get(suffix, ()))
    if not ok:
        raise VaultError(
            f"Downloaded data is not a valid {suffix} image "
            f"(the URL probably returned an error page): {filename}"
        )


def register_tools(mcp: FastMCP, vault: Vault) -> None:
    def _attachment_path(filename: str, folder: str) -> str:
        if "/" in filename or "\\" in filename:
            raise VaultError(
                f"filename must be a bare filename, not a path (use folder=): {filename}"
            )
        base = folder.strip().strip("/") if folder else vault.attachment_folder()
        return f"{base}/{filename}" if base else filename

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
    def replace_in_note(
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        expected_replacements: int | None = None,
    ) -> str:
        """Safely replace exact text inside an existing Markdown note without
        rewriting unrelated content.

        Args:
            path: Vault-relative path of an existing note.
            old_text: Exact text that must already occur in the note.
            new_text: Replacement text.
            replace_all: Replace every exact occurrence instead of only the first.
            expected_replacements: Abort without writing unless exactly this many
                replacements would be made. When replace_all is false the planned
                count is always 1, so to assert "exactly N occurrences exist" pass
                replace_all=true with expected_replacements=N.
        """
        count = vault.replace_in_note(
            path, old_text, new_text, replace_all, expected_replacements
        )
        plural = "occurrence" if count == 1 else "occurrences"
        return f"Replaced {count} {plural} in {path}"

    @mcp.tool()
    def write_attachment(
        filename: str, base64_data: str, folder: str = "", overwrite: bool = False
    ) -> str:
        """Save a base64-encoded image into the vault so a note can embed it.

        Returns the saved path and a ready-to-use "![[file.png]]" embed to pass
        to append_note. Use this for small images only — prefer fetch_attachment
        when the image has a URL, since a large PNG does not survive base64 in a
        single tool call.

        Args:
            filename: Bare filename with an image extension, e.g. "chart.png".
            base64_data: Base64 image bytes; a "data:image/png;base64," prefix is fine.
            folder: Vault-relative folder; empty uses the vault's attachment folder.
            overwrite: Must be true to replace an existing attachment.
        """
        target = _attachment_path(filename, folder)
        payload = "".join(base64_data.split())
        if payload.startswith("data:"):
            _, _, payload = payload.partition(",")
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise VaultError(f"base64_data is not valid base64: {filename}")
        saved = vault.write_attachment(target, data, overwrite)
        return _attachment_result(saved, len(data))

    @mcp.tool()
    def fetch_attachment(
        filename: str, url: str, folder: str = "", overwrite: bool = False
    ) -> str:
        """Download an image from an http(s) URL into the vault.

        Preferred over write_attachment for generated or large images: the bytes
        are fetched by the server and never pass through the conversation.
        Returns the saved path and a ready-to-use "![[file.png]]" embed to pass
        to append_note.

        Args:
            filename: Bare filename with an image extension, e.g. "chart.png".
            url: http or https URL of the image.
            folder: Vault-relative folder; empty uses the vault's attachment folder.
            overwrite: Must be true to replace an existing attachment.
        """
        target = _attachment_path(filename, folder)
        if urlparse(url).scheme not in ("http", "https"):
            raise VaultError(f"Only http and https URLs are supported: {url}")
        limit = vault.MAX_ATTACHMENT_BYTES
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                if urlparse(response.url).scheme not in ("http", "https"):
                    raise VaultError(
                        f"URL redirected to an unsupported scheme: {response.url}"
                    )
                data = response.read(limit + 1)
        except (urllib.error.URLError, OSError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise VaultError(f"Cannot download {url}: {reason}")
        if len(data) > limit:
            raise VaultError(f"Download exceeds the {limit} byte limit: {url}")
        _check_magic(filename, data)
        saved = vault.write_attachment(target, data, overwrite)
        return _attachment_result(saved, len(data))

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
