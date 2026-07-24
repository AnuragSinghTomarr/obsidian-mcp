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

    def list_notes(self, folder: str = "", recursive: bool = True) -> dict[str, list[str]]:
        base = self._resolve(folder, require_md=False) if folder else self.root
        if not base.is_dir():
            raise VaultError(f"Folder not found: {folder}")
        pattern = "**/*" if recursive else "*"
        notes: list[str] = []
        folders: list[str] = []
        for p in sorted(base.glob(pattern)):
            rel = p.relative_to(self.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if p.is_dir():
                folders.append(rel.as_posix())
            elif p.suffix == ".md":
                notes.append(rel.as_posix())
        return {"notes": notes, "folders": folders}

    def read_note(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise VaultError(f"Note not found: {path}")
        return p.read_text(encoding="utf-8")
