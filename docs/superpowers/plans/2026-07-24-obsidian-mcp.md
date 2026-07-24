# Obsidian MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python MCP server exposing 7 read/write tools over a local Obsidian vault, runnable over stdio (Claude) or streamable HTTP (ChatGPT connector).

**Architecture:** All filesystem logic lives in a `Vault` class (`vault.py`) that enforces path confinement, hides dotfiles, and soft-deletes to `.trash/`. `tools.py` is a thin FastMCP tool layer delegating to `Vault`. `server.py` holds the CLI (vault path, transport, port) and builds the server.

**Tech Stack:** Python ≥3.11, uv, official `mcp` SDK (FastMCP), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-24-obsidian-mcp-design.md`

**Execution note:** Coding subagents run on **Opus 4.8** (`model: "opus"`). The main thread (Fable 5) reviews after every task. Per-task commits are pre-approved by the user for this plan.

## Global Constraints

- Python `>=3.11`; environment managed exclusively with **uv** (never pip/npm). Runtime dependency: `mcp` only.
- All tool paths are vault-relative. Absolute paths, `../` escapes, and any dot-prefixed path component (`.obsidian`, `.trash`, …) are rejected with `VaultError`.
- Note operations require a `.md` suffix; other file types are invisible in v1.
- `delete_note` never hard-deletes: move to `.trash/` at vault root, collision-suffixed `name 1.md`, `name 2.md`, ….
- Search is case-insensitive substring, capped at 50 matches, context ±1 line.
- Errors surface as human-readable messages (`VaultError`), never stack traces.
- Transports: `stdio` (default) and `http` = streamable HTTP on `127.0.0.1`, default port `8757`.
- TDD for every behavior; run tests with `uv run pytest`.

---

### Task 1: Project scaffolding + Vault path confinement

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/obsidian_mcp/__init__.py`, `src/obsidian_mcp/vault.py`
- Test: `tests/conftest.py`, `tests/test_vault.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `VaultError(Exception)`; `Vault(root: Path)` raising `VaultError` if `root` is not a directory; `Vault._resolve(rel: str, *, require_md: bool = True) -> Path` (internal, but later tasks rely on its rules); fixtures `vault_dir` and `vault` in `tests/conftest.py`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "obsidian-mcp"
version = "0.1.0"
description = "MCP server exposing read/write tools over a local Obsidian vault"
requires-python = ">=3.11"
dependencies = ["mcp>=1.2.0"]

[project.scripts]
obsidian-mcp = "obsidian_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/obsidian_mcp"]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.25"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
dist/
```

- [ ] **Step 3: Create package and sync environment**

```bash
mkdir -p src/obsidian_mcp tests
printf '' > src/obsidian_mcp/__init__.py
uv sync
```

Expected: `uv sync` resolves, creates `.venv/` and `uv.lock`, installs `mcp`, `pytest`, `pytest-asyncio`, and the project itself (editable).

Note: `server.py` doesn't exist yet, so the `obsidian-mcp` console script is not runnable until Task 6 — that's fine; `uv sync` does not import it.

- [ ] **Step 4: Write test fixtures — `tests/conftest.py`**

```python
import pytest

from obsidian_mcp.vault import Vault


@pytest.fixture
def vault_dir(tmp_path):
    (tmp_path / "Daily").mkdir()
    (tmp_path / "Projects" / "Solar").mkdir(parents=True)
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "Inbox.md").write_text("# Inbox\ncapture things here\n", encoding="utf-8")
    (tmp_path / "Daily" / "2026-07-24.md").write_text(
        "# Today\n- solar inverter reading\n", encoding="utf-8"
    )
    (tmp_path / "Projects" / "Solar" / "Deye.md").write_text(
        "# Deye SUN-12K\nhybrid inverter notes\n", encoding="utf-8"
    )
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (tmp_path / "diagram.png").write_bytes(b"\x89PNG")
    return tmp_path


@pytest.fixture
def vault(vault_dir):
    return Vault(vault_dir)
```

- [ ] **Step 5: Write failing tests — `tests/test_vault.py`**

```python
import pytest

from obsidian_mcp.vault import Vault, VaultError


class TestVaultInit:
    def test_rejects_missing_directory(self, tmp_path):
        with pytest.raises(VaultError, match="not a directory"):
            Vault(tmp_path / "nope")

    def test_accepts_directory(self, vault_dir):
        assert Vault(vault_dir).root == vault_dir.resolve()


class TestResolve:
    def test_valid_relative_path(self, vault, vault_dir):
        assert vault._resolve("Inbox.md") == vault_dir / "Inbox.md"

    def test_rejects_traversal(self, vault):
        with pytest.raises(VaultError, match="escapes the vault"):
            vault._resolve("../outside.md")

    def test_rejects_absolute_path(self, vault):
        with pytest.raises(VaultError, match="Absolute paths"):
            vault._resolve("/etc/passwd.md")

    def test_rejects_hidden_parts(self, vault):
        with pytest.raises(VaultError, match="Hidden"):
            vault._resolve(".obsidian/app.md")
        with pytest.raises(VaultError, match="Hidden"):
            vault._resolve(".trash/gone.md")

    def test_rejects_non_markdown(self, vault):
        with pytest.raises(VaultError, match="Only .md"):
            vault._resolve("diagram.png")

    def test_folder_mode_allows_non_markdown(self, vault, vault_dir):
        assert vault._resolve("Daily", require_md=False) == vault_dir / "Daily"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -v`
Expected: FAIL — `ImportError` / `ModuleNotFoundError` (no `vault.py` yet).

- [ ] **Step 7: Write `src/obsidian_mcp/vault.py`**

```python
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
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock .gitignore src/ tests/
git commit -m "feat: scaffold uv project; Vault path confinement"
```

---

### Task 2: Vault read operations — `list_notes`, `read_note`

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (add methods to `Vault`)
- Test: `tests/test_vault.py` (append classes)

**Interfaces:**
- Consumes: `Vault._resolve`, `VaultError`, fixtures from Task 1.
- Produces: `Vault.list_notes(folder: str = "", recursive: bool = True) -> dict[str, list[str]]` returning `{"notes": [...], "folders": [...]}` of sorted vault-relative POSIX-style strings; `Vault.read_note(path: str) -> str`.

- [ ] **Step 1: Append failing tests to `tests/test_vault.py`**

```python
class TestListNotes:
    def test_recursive_lists_all_notes(self, vault):
        result = vault.list_notes()
        assert result["notes"] == [
            "Daily/2026-07-24.md",
            "Inbox.md",
            "Projects/Solar/Deye.md",
        ]

    def test_hides_dot_folders_and_non_md(self, vault):
        result = vault.list_notes()
        joined = " ".join(result["notes"] + result["folders"])
        assert ".obsidian" not in joined
        assert "diagram.png" not in joined

    def test_lists_folders(self, vault):
        assert vault.list_notes()["folders"] == ["Daily", "Projects", "Projects/Solar"]

    def test_non_recursive(self, vault):
        result = vault.list_notes(recursive=False)
        assert result["notes"] == ["Inbox.md"]
        assert result["folders"] == ["Daily", "Projects"]

    def test_subfolder(self, vault):
        result = vault.list_notes("Projects")
        assert result["notes"] == ["Projects/Solar/Deye.md"]

    def test_missing_folder_errors(self, vault):
        with pytest.raises(VaultError, match="Folder not found"):
            vault.list_notes("Nope")


class TestReadNote:
    def test_reads_content(self, vault):
        assert vault.read_note("Inbox.md") == "# Inbox\ncapture things here\n"

    def test_missing_note_errors(self, vault):
        with pytest.raises(VaultError, match="Note not found"):
            vault.read_note("ghost.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -v`
Expected: new tests FAIL with `AttributeError` (`list_notes`/`read_note` undefined); Task 1 tests still PASS.

- [ ] **Step 3: Add methods to `Vault` in `src/obsidian_mcp/vault.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: Vault.list_notes and Vault.read_note"
```

---

### Task 3: Vault write operations — `write_note`, `append_note`

**Files:**
- Modify: `src/obsidian_mcp/vault.py`
- Test: `tests/test_vault.py` (append classes)

**Interfaces:**
- Consumes: `Vault._resolve`, `VaultError`, fixtures.
- Produces: `Vault.write_note(path: str, content: str, overwrite: bool = False) -> None`; `Vault.append_note(path: str, content: str) -> None`.

- [ ] **Step 1: Append failing tests to `tests/test_vault.py`**

```python
class TestWriteNote:
    def test_creates_note_with_parents(self, vault, vault_dir):
        vault.write_note("Areas/Health/log.md", "# Log\n")
        assert (vault_dir / "Areas" / "Health" / "log.md").read_text(encoding="utf-8") == "# Log\n"

    def test_existing_without_overwrite_errors(self, vault):
        with pytest.raises(VaultError, match="already exists"):
            vault.write_note("Inbox.md", "clobber")

    def test_overwrite_replaces(self, vault):
        vault.write_note("Inbox.md", "fresh\n", overwrite=True)
        assert vault.read_note("Inbox.md") == "fresh\n"

    def test_rejects_traversal(self, vault):
        with pytest.raises(VaultError, match="escapes the vault"):
            vault.write_note("../evil.md", "x")


class TestAppendNote:
    def test_appends_with_newline_separator(self, vault, vault_dir):
        (vault_dir / "NoNewline.md").write_text("tail", encoding="utf-8")
        vault.append_note("NoNewline.md", "added\n")
        assert vault.read_note("NoNewline.md") == "tail\nadded\n"

    def test_appends_to_newline_terminated(self, vault):
        vault.append_note("Inbox.md", "- new item\n")
        assert vault.read_note("Inbox.md") == "# Inbox\ncapture things here\n- new item\n"

    def test_missing_note_errors(self, vault):
        with pytest.raises(VaultError, match="Note not found"):
            vault.append_note("ghost.md", "x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -v`
Expected: new tests FAIL with `AttributeError`; existing tests PASS.

- [ ] **Step 3: Add methods to `Vault`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: Vault.write_note and Vault.append_note"
```

---

### Task 4: Vault move + soft delete — `move_note`, `delete_note`

**Files:**
- Modify: `src/obsidian_mcp/vault.py`
- Test: `tests/test_vault.py` (append classes)

**Interfaces:**
- Consumes: `Vault._resolve`, `VaultError`, fixtures.
- Produces: `Vault.move_note(source: str, destination: str) -> None`; `Vault.delete_note(path: str) -> str` returning the vault-relative trash path (e.g. `".trash/Inbox.md"`).

- [ ] **Step 1: Append failing tests to `tests/test_vault.py`**

```python
class TestMoveNote:
    def test_moves_creating_folders(self, vault, vault_dir):
        vault.move_note("Inbox.md", "Archive/2026/Inbox.md")
        assert not (vault_dir / "Inbox.md").exists()
        assert vault.read_note("Archive/2026/Inbox.md") == "# Inbox\ncapture things here\n"

    def test_missing_source_errors(self, vault):
        with pytest.raises(VaultError, match="Note not found"):
            vault.move_note("ghost.md", "x.md")

    def test_existing_destination_errors(self, vault):
        with pytest.raises(VaultError, match="already exists"):
            vault.move_note("Inbox.md", "Daily/2026-07-24.md")


class TestDeleteNote:
    def test_moves_to_trash(self, vault, vault_dir):
        assert vault.delete_note("Inbox.md") == ".trash/Inbox.md"
        assert not (vault_dir / "Inbox.md").exists()
        assert (vault_dir / ".trash" / "Inbox.md").is_file()

    def test_collision_gets_suffix(self, vault, vault_dir):
        vault.delete_note("Inbox.md")
        vault.write_note("Inbox.md", "second\n")
        assert vault.delete_note("Inbox.md") == ".trash/Inbox 1.md"
        assert (vault_dir / ".trash" / "Inbox 1.md").read_text(encoding="utf-8") == "second\n"

    def test_missing_note_errors(self, vault):
        with pytest.raises(VaultError, match="Note not found"):
            vault.delete_note("ghost.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -v`
Expected: new tests FAIL with `AttributeError`; existing tests PASS.

- [ ] **Step 3: Add methods to `Vault`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: Vault.move_note and trash-based Vault.delete_note"
```

---

### Task 5: Vault search — `search_notes`

**Files:**
- Modify: `src/obsidian_mcp/vault.py`
- Test: `tests/test_vault.py` (append class)

**Interfaces:**
- Consumes: `Vault.list_notes`, `VaultError`, fixtures.
- Produces: `Vault.MAX_MATCHES = 50` (class attribute); `Vault.search_notes(query: str, folder: str = "") -> list[dict]` where each match is `{"path": str, "line": int, "context": str}`; `line` is 1-based, `0` for filename matches; `context` is the matching line ±1 line.

- [ ] **Step 1: Append failing tests to `tests/test_vault.py`**

```python
class TestSearchNotes:
    def test_finds_content_case_insensitive(self, vault):
        matches = vault.search_notes("INVERTER")
        paths = {m["path"] for m in matches}
        assert paths == {"Daily/2026-07-24.md", "Projects/Solar/Deye.md"}

    def test_match_shape_and_context(self, vault):
        (m,) = vault.search_notes("capture")
        assert m["path"] == "Inbox.md"
        assert m["line"] == 2
        assert m["context"] == "# Inbox\ncapture things here"

    def test_filename_match(self, vault):
        matches = vault.search_notes("deye")
        assert {"path": "Projects/Solar/Deye.md", "line": 0, "context": "(filename match)"} in matches

    def test_folder_scoping(self, vault):
        assert all(
            m["path"].startswith("Projects/")
            for m in vault.search_notes("inverter", folder="Projects")
        )

    def test_empty_query_errors(self, vault):
        with pytest.raises(VaultError, match="must not be empty"):
            vault.search_notes("")

    def test_cap_at_max_matches(self, vault):
        vault.write_note("Big.md", "needle\n" * 200)
        assert len(vault.search_notes("needle")) == Vault.MAX_MATCHES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -v`
Expected: new tests FAIL with `AttributeError`; existing tests PASS.

- [ ] **Step 3: Add to `Vault` (class attribute + method)**

```python
    MAX_MATCHES = 50
```

(place at the top of the class body, above `__init__`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: Vault.search_notes with cap and context"
```

---

### Task 6: MCP tool layer + server CLI

**Files:**
- Create: `src/obsidian_mcp/tools.py`, `src/obsidian_mcp/server.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: full `Vault` API from Tasks 1–5.
- Produces: `register_tools(mcp: FastMCP, vault: Vault) -> None` registering all 7 tools; `build_server(vault_path: Path, host: str = "127.0.0.1", port: int = 8757) -> FastMCP`; `main() -> None` console entry point (already wired in `pyproject.toml` as `obsidian-mcp`).

- [ ] **Step 1: Write failing tests — `tests/test_tools.py`**

Note: `FastMCP.call_tool` is async (covered by `asyncio_mode = "auto"`). Its return shape differs across `mcp` SDK versions (list of content blocks vs. tuple of content + raw); `result_text` handles both.

```python
import json

import pytest

from obsidian_mcp.server import build_server


@pytest.fixture
def server(vault_dir):
    return build_server(vault_dir)


def result_text(res) -> str:
    content = res[0] if isinstance(res, tuple) else res
    return "\n".join(b.text for b in content if hasattr(b, "text"))


async def test_all_seven_tools_registered(server):
    names = {t.name for t in await server.list_tools()}
    assert names == {
        "list_notes",
        "read_note",
        "write_note",
        "append_note",
        "search_notes",
        "move_note",
        "delete_note",
    }


async def test_list_notes_tool(server):
    data = json.loads(result_text(await server.call_tool("list_notes", {})))
    assert "Inbox.md" in data["notes"]


async def test_read_note_tool(server):
    res = await server.call_tool("read_note", {"path": "Inbox.md"})
    assert "capture things here" in result_text(res)


async def test_write_then_read_roundtrip(server):
    await server.call_tool("write_note", {"path": "New.md", "content": "hello\n"})
    assert result_text(await server.call_tool("read_note", {"path": "New.md"})) == "hello\n"


async def test_append_note_tool(server):
    await server.call_tool("append_note", {"path": "Inbox.md", "content": "- item\n"})
    assert "- item" in result_text(await server.call_tool("read_note", {"path": "Inbox.md"}))


async def test_search_notes_tool(server):
    data = json.loads(result_text(await server.call_tool("search_notes", {"query": "inverter"})))
    assert any(m["path"] == "Projects/Solar/Deye.md" for m in data)


async def test_move_note_tool(server, vault_dir):
    await server.call_tool("move_note", {"source": "Inbox.md", "destination": "Archive/Inbox.md"})
    assert not (vault_dir / "Inbox.md").exists()
    res = await server.call_tool("read_note", {"path": "Archive/Inbox.md"})
    assert "capture things here" in result_text(res)


async def test_delete_note_tool(server, vault_dir):
    res = await server.call_tool("delete_note", {"path": "Inbox.md"})
    assert ".trash/Inbox.md" in result_text(res)
    assert (vault_dir / ".trash" / "Inbox.md").is_file()


async def test_vault_error_surfaces_cleanly(server):
    with pytest.raises(Exception, match="Note not found"):
        await server.call_tool("read_note", {"path": "ghost.md"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: obsidian_mcp.server`.

- [ ] **Step 3: Write `src/obsidian_mcp/tools.py`**

```python
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
```

- [ ] **Step 4: Write `src/obsidian_mcp/server.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: entire suite PASSES (vault + tools).

If `server.call_tool` / `server.list_tools` don't exist on the installed `mcp` version, check the installed SDK (`uv run python -c "import mcp; print(mcp.__version__)"`) and adapt the test helper — the public alternative is `mcp.shared.memory.create_connected_server_and_client_session(server._mcp_server)`. Keep assertions unchanged.

- [ ] **Step 6: CLI smoke checks**

```bash
uv run obsidian-mcp --help
uv run obsidian-mcp 2>&1 | head -1   # with OBSIDIAN_VAULT_PATH unset
OBSIDIAN_VAULT_PATH=/nonexistent uv run obsidian-mcp 2>&1 | head -1
```

Expected: help text; `error: set OBSIDIAN_VAULT_PATH or pass --vault <path>`; `error: Vault path is not a directory: /nonexistent`.

(Unset `OBSIDIAN_VAULT_PATH` for the second command: `env -u OBSIDIAN_VAULT_PATH uv run obsidian-mcp`.)

- [ ] **Step 7: Commit**

```bash
git add src/obsidian_mcp/tools.py src/obsidian_mcp/server.py tests/test_tools.py
git commit -m "feat: MCP tool layer and server CLI (stdio + streamable HTTP)"
```

---

### Task 7: README + final verification

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: user-facing setup docs.

- [ ] **Step 1: Write `README.md`**

```markdown
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

Safety: all paths are confined to the vault (no `../`, no absolute paths),
dot-folders like `.obsidian/` are invisible, and deletion is always soft.

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
```

- [ ] **Step 2: Full suite + smoke verification**

```bash
uv run pytest -v
uv run obsidian-mcp --help
```

Expected: all tests PASS; help prints.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with setup for Claude and ChatGPT"
```
