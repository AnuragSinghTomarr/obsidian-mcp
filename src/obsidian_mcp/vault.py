from __future__ import annotations

from pathlib import Path


class VaultError(Exception):
    """A vault operation failed for a reason the caller can fix."""


class Vault:
    def __init__(self, root: Path) -> None:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise VaultError(f"Vault path is not a directory: {root}")
        self.root = root

    def _resolve(self, rel: str, *, require_md: bool = True) -> Path:
        if Path(rel).is_absolute():
            raise VaultError(f"Absolute paths are not allowed: {rel}")
        candidate = (self.root / rel).resolve()
        if not candidate.is_relative_to(self.root):
            raise VaultError(f"Path escapes the vault: {rel}")
        relative = candidate.relative_to(self.root)
        if any(part.startswith(".") for part in relative.parts):
            raise VaultError(f"Hidden files and folders are not accessible: {rel}")
        if require_md and candidate.suffix != ".md":
            raise VaultError(f"Only .md notes are supported: {rel}")
        return candidate
