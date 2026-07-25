from __future__ import annotations

from pathlib import Path


class VaultError(Exception):
    """A vault operation failed for a reason the caller can fix."""


class Vault:
    MAX_MATCHES = 50

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

    def write_note(self, path: str, content: str, overwrite: bool = False) -> None:
        p = self._resolve(path)
        if p.exists() and not overwrite:
            raise VaultError(
                f"Note already exists (pass overwrite=true to replace): {path}"
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def append_note(self, path: str, content: str) -> None:
        p = self._resolve(path)
        if not p.is_file():
            raise VaultError(f"Note not found: {path}")
        existing = p.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
        p.write_text(existing + content, encoding="utf-8")

    def move_note(self, source: str, destination: str) -> None:
        src = self._resolve(source)
        dst = self._resolve(destination)
        if not src.is_file():
            raise VaultError(f"Note not found: {source}")
        if dst.exists():
            raise VaultError(f"Destination already exists: {destination}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)

    def delete_note(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise VaultError(f"Note not found: {path}")
        trash = self.root / ".trash"
        trash.mkdir(exist_ok=True)
        target = trash / p.name
        counter = 1
        while target.exists():
            target = trash / f"{p.stem} {counter}{p.suffix}"
            counter += 1
        p.rename(target)
        return target.relative_to(self.root).as_posix()

    def search_notes(self, query: str, folder: str = "") -> list[dict]:
        if not query:
            raise VaultError("Search query must not be empty")
        needle = query.lower()
        matches: list[dict] = []
        for rel in self.list_notes(folder, recursive=True)["notes"]:
            note = self.root / rel
            if needle in note.name.lower():
                matches.append({"path": rel, "line": 0, "context": "(filename match)"})
            lines = note.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if needle in line.lower():
                    context = "\n".join(lines[max(0, i - 1) : i + 2])
                    matches.append({"path": rel, "line": i + 1, "context": context})
            if len(matches) >= self.MAX_MATCHES:
                return matches[: self.MAX_MATCHES]
        return matches
