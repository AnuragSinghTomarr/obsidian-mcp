# replace_in_note + Atomic Note Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `replace_in_note` MCP tool for surgical text edits, make all note writes atomic (temp file + rename), and cap note size at 10 MB.

**Architecture:** All new filesystem behavior goes into `Vault` (`src/obsidian_mcp/vault.py`); `tools.py` gains one thin `@mcp.tool()` wrapper with no path logic. A private `Vault._write_text_atomic()` helper is shared by `write_note`, `append_note`, and the new `replace_in_note`.

**Tech Stack:** Python ≥3.11, stdlib only (`tempfile`, `os`, `pathlib`), FastMCP (`mcp>=1.2.0`), pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-08-04-note-editing-tools-design.md`

## Global Constraints

- Run everything with `uv` — never `pip` or bare `python` (`uv run pytest -q`, `uv run python …`).
- All path validation stays in `Vault._resolve()` — no path handling in `tools.py`.
- Every failure raises `VaultError` with a message naming the offending path/argument. No bare `except`.
- Tools return human-readable strings, never `{"success": …}` JSON.
- Tests use the `vault` / `vault_dir` / `server` fixtures from `tests/conftest.py` — do not build ad-hoc trees.
- No `@pytest.mark.asyncio` (asyncio_mode is auto).
- README tool table updated in the same commit as the tool.
- Never commit a real vault path or vault contents.
- Full suite (`uv run pytest -q`) must pass before every commit; suite currently has 90 tests.

---

### Task 1: Atomic write helper + note size cap in `Vault`

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (imports; new constant + helper; `write_note` lines 91–103; `append_note` lines 127–134)
- Test: `tests/test_vault.py`

**Interfaces:**
- Produces: `Vault.MAX_NOTE_BYTES: int` and `Vault._write_text_atomic(p: Path, content: str, rel: str) -> None` — raises `VaultError` if the UTF-8 encoding of `content` exceeds `MAX_NOTE_BYTES` or if the write fails; on any failure the target file is untouched and no temp file remains. Task 2 calls this helper.

- [ ] **Step 1: Write the failing tests** — add to `tests/test_vault.py` (new class, near `TestWriteNote`):

```python
class TestAtomicNoteWrites:
    def test_failed_write_leaves_original_intact(self, vault, vault_dir, monkeypatch):
        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(VaultError, match="Inbox.md"):
            vault.write_note("Inbox.md", "new content", overwrite=True)
        assert (vault_dir / "Inbox.md").read_text(encoding="utf-8") == "# Inbox\ncapture things here\n"

    def test_failed_write_leaves_no_temp_file(self, vault, vault_dir, monkeypatch):
        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(VaultError):
            vault.write_note("Inbox.md", "new content", overwrite=True)
        leftovers = [p for p in vault_dir.iterdir() if ".Inbox.md." in p.name]
        assert leftovers == []

    def test_overwrite_preserves_permission_bits(self, vault, vault_dir):
        note = vault_dir / "Inbox.md"
        note.chmod(0o600)
        vault.write_note("Inbox.md", "rewritten\n", overwrite=True)
        assert (note.stat().st_mode & 0o777) == 0o600

    def test_rejects_oversized_note(self, vault, vault_dir):
        big = "x" * (Vault.MAX_NOTE_BYTES + 1)
        with pytest.raises(VaultError, match="byte limit"):
            vault.write_note("Inbox.md", big, overwrite=True)
        assert (vault_dir / "Inbox.md").read_text(encoding="utf-8") == "# Inbox\ncapture things here\n"

    def test_size_cap_counts_utf8_bytes(self, vault):
        # 4-byte emoji: char count is far under the cap, byte count is over it
        big = "🚀" * (Vault.MAX_NOTE_BYTES // 4 + 1)
        with pytest.raises(VaultError, match="byte limit"):
            vault.write_note("Emoji.md", big)

    def test_append_is_atomic(self, vault, vault_dir, monkeypatch):
        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(VaultError):
            vault.append_note("Inbox.md", "- more\n")
        assert (vault_dir / "Inbox.md").read_text(encoding="utf-8") == "# Inbox\ncapture things here\n"
```

`tests/test_vault.py` already imports `pytest`, `Vault`, and `VaultError` — check the top of the file and add whatever is missing.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_vault.py::TestAtomicNoteWrites -v`
Expected: FAIL — `AttributeError: … has no attribute 'MAX_NOTE_BYTES'` and/or the monkeypatched `os.replace` never firing (current code writes in place, so the "original intact" assertions fail).

- [ ] **Step 3: Implement in `src/obsidian_mcp/vault.py`**

Add imports at the top (keep existing ones):

```python
import os
import tempfile
```

Add the constant next to `MAX_ATTACHMENT_BYTES`:

```python
MAX_NOTE_BYTES = 10 * 1024 * 1024
```

Add the helper after `_resolve_attachment`:

```python
def _write_text_atomic(self, p: Path, content: str, rel: str) -> None:
    """Write a note via temp-file + rename so a failure never truncates it."""
    data = content.encode("utf-8")
    if len(data) > self.MAX_NOTE_BYTES:
        raise VaultError(
            f"Note is {len(data)} bytes, over the "
            f"{self.MAX_NOTE_BYTES} byte limit: {rel}"
        )
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        try:
            if p.exists():
                os.fchmod(fd, p.stat().st_mode & 0o7777)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, p)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise VaultError(f"Cannot write {rel}: {exc}")
```

Replace the body of `write_note` so mkdir keeps its existing error message and the write goes through the helper:

```python
def write_note(self, path: str, content: str, overwrite: bool = False) -> None:
    p = self._resolve(path)
    if p.exists() and not overwrite:
        raise VaultError(
            f"Note already exists (pass overwrite=true to replace): {path}"
        )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise VaultError(
            f"Cannot write {path}: a parent path component is an existing note"
        )
    self._write_text_atomic(p, content, path)
```

Replace the final line of `append_note` (`p.write_text(...)`) with:

```python
self._write_text_atomic(p, existing + content, path)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (90 existing + 6 new). If `test_write_note_parent_is_file` fails, the mkdir try/except was lost — restore it exactly as above.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: atomic note writes with 10 MB size cap"
```

---

### Task 2: `Vault.replace_in_note()`

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (new method after `write_note`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `Vault._write_text_atomic(p, content, rel)` from Task 1.
- Produces: `Vault.replace_in_note(path: str, old_text: str, new_text: str, replace_all: bool = False, expected_replacements: int | None = None) -> int` — returns the number of replacements made; raises `VaultError` on: empty `old_text`, missing note, `old_text` not found, `expected_replacements` mismatch, unsafe path. Task 3 calls this.

- [ ] **Step 1: Write the failing tests** — add to `tests/test_vault.py`:

```python
class TestReplaceInNote:
    def test_replaces_first_occurrence_only(self, vault, vault_dir):
        (vault_dir / "Embed.md").write_text("a ![](x.png) b ![](x.png) c\n", encoding="utf-8")
        count = vault.replace_in_note("Embed.md", "![](x.png)", "![[x.png]]")
        assert count == 1
        assert (vault_dir / "Embed.md").read_text(encoding="utf-8") == "a ![[x.png]] b ![](x.png) c\n"

    def test_replace_all(self, vault, vault_dir):
        (vault_dir / "Embed.md").write_text("![](x.png)\n![](x.png)\n", encoding="utf-8")
        count = vault.replace_in_note("Embed.md", "![](x.png)", "![[x.png]]", replace_all=True)
        assert count == 2
        assert (vault_dir / "Embed.md").read_text(encoding="utf-8") == "![[x.png]]\n![[x.png]]\n"

    def test_preserves_unrelated_content(self, vault, vault_dir):
        original = "# Today\n- solar inverter reading\n"
        vault.replace_in_note("Daily/2026-07-24.md", "solar", "hybrid")
        updated = (vault_dir / "Daily" / "2026-07-24.md").read_text(encoding="utf-8")
        assert updated == original.replace("solar", "hybrid", 1)

    def test_old_text_absent_errors_and_leaves_file(self, vault, vault_dir):
        with pytest.raises(VaultError, match="old_text not found"):
            vault.replace_in_note("Inbox.md", "no such text", "x")
        assert (vault_dir / "Inbox.md").read_text(encoding="utf-8") == "# Inbox\ncapture things here\n"

    def test_expected_replacements_mismatch_aborts(self, vault, vault_dir):
        (vault_dir / "Embed.md").write_text("![](x.png)\n![](x.png)\n", encoding="utf-8")
        with pytest.raises(VaultError, match="Expected 1"):
            vault.replace_in_note(
                "Embed.md", "![](x.png)", "![[x.png]]",
                replace_all=True, expected_replacements=1,
            )
        assert (vault_dir / "Embed.md").read_text(encoding="utf-8") == "![](x.png)\n![](x.png)\n"

    def test_expected_replacements_match_succeeds(self, vault, vault_dir):
        (vault_dir / "Embed.md").write_text("![](x.png)\n![](x.png)\n", encoding="utf-8")
        count = vault.replace_in_note(
            "Embed.md", "![](x.png)", "![[x.png]]",
            replace_all=True, expected_replacements=2,
        )
        assert count == 2

    def test_empty_old_text_errors(self, vault):
        with pytest.raises(VaultError, match="old_text"):
            vault.replace_in_note("Inbox.md", "", "x")

    def test_missing_note_errors(self, vault):
        with pytest.raises(VaultError, match="Nope.md"):
            vault.replace_in_note("Nope.md", "a", "b")

    def test_rejects_traversal(self, vault):
        with pytest.raises(VaultError):
            vault.replace_in_note("../outside.md", "a", "b")

    def test_rejects_absolute_path(self, vault):
        with pytest.raises(VaultError):
            vault.replace_in_note("/etc/notes.md", "a", "b")

    def test_rejects_non_markdown(self, vault):
        with pytest.raises(VaultError):
            vault.replace_in_note("diagram.png", "a", "b")

    def test_rejects_escaping_symlink(self, vault, vault_dir, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (outside / "secret.md").write_text("token here\n", encoding="utf-8")
        (vault_dir / "link.md").symlink_to(outside / "secret.md")
        with pytest.raises(VaultError):
            vault.replace_in_note("link.md", "token", "x")
        assert (outside / "secret.md").read_text(encoding="utf-8") == "token here\n"

    def test_unicode_content(self, vault, vault_dir):
        (vault_dir / "Uni.md").write_text("héllo 🚀 日本語\n", encoding="utf-8")
        vault.replace_in_note("Uni.md", "🚀", "🌞")
        assert (vault_dir / "Uni.md").read_text(encoding="utf-8") == "héllo 🌞 日本語\n"

    def test_filename_with_spaces_ampersand_hyphen(self, vault, vault_dir):
        folder = vault_dir / "System Design Course"
        folder.mkdir()
        note = folder / "04 - HTTP & APIs.md"
        note.write_text("![](attachments/5xx-http-errors-handwritten.png)\n", encoding="utf-8")
        count = vault.replace_in_note(
            "System Design Course/04 - HTTP & APIs.md",
            "![](attachments/5xx-http-errors-handwritten.png)",
            "![[5xx-http-errors-handwritten.png]]",
            expected_replacements=1,
        )
        assert count == 1
        assert note.read_text(encoding="utf-8") == "![[5xx-http-errors-handwritten.png]]\n"

    def test_oversized_result_aborts(self, vault, vault_dir):
        (vault_dir / "Grow.md").write_text("SEED\n", encoding="utf-8")
        with pytest.raises(VaultError, match="byte limit"):
            vault.replace_in_note("Grow.md", "SEED", "x" * (Vault.MAX_NOTE_BYTES + 1))
        assert (vault_dir / "Grow.md").read_text(encoding="utf-8") == "SEED\n"
```

Note on the symlink test: `_resolve()` rejects the escaping symlink because the resolved path is outside the root — the test documents that `replace_in_note` inherits this.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_vault.py::TestReplaceInNote -v`
Expected: FAIL — `AttributeError: 'Vault' object has no attribute 'replace_in_note'`.

- [ ] **Step 3: Implement** — add after `write_note` in `src/obsidian_mcp/vault.py`:

```python
def replace_in_note(
    self,
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    expected_replacements: int | None = None,
) -> int:
    if not old_text:
        raise VaultError("old_text must not be empty")
    p = self._resolve(path)
    if not p.is_file():
        raise VaultError(f"Note not found: {path}")
    content = p.read_text(encoding="utf-8")
    occurrences = content.count(old_text)
    if occurrences == 0:
        raise VaultError(f"old_text not found in {path}")
    planned = occurrences if replace_all else 1
    if expected_replacements is not None and planned != expected_replacements:
        raise VaultError(
            f"Expected {expected_replacements} replacement(s) but would make "
            f"{planned} in {path}"
        )
    if replace_all:
        updated = content.replace(old_text, new_text)
    else:
        updated = content.replace(old_text, new_text, 1)
    self._write_text_atomic(p, updated, path)
    return planned
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: Vault.replace_in_note with expected_replacements guard"
```

---

### Task 3: `replace_in_note` MCP tool + README

**Files:**
- Modify: `src/obsidian_mcp/tools.py` (new tool after `write_note`, i.e. after line 91)
- Modify: `tests/test_tools.py` (`test_all_registered_tools` plus new tests)
- Modify: `README.md` (tool table, lines 24–34, and a usage example)

**Interfaces:**
- Consumes: `Vault.replace_in_note(path, old_text, new_text, replace_all, expected_replacements) -> int` from Task 2.
- Produces: MCP tool `replace_in_note` returning `"Replaced <n> occurrence(s) in <path>"`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_tools.py`, add `"replace_in_note"` to the expected set in `test_all_registered_tools`, then add:

```python
async def test_replace_in_note_tool(server):
    await server.call_tool("write_note", {"path": "Embed.md", "content": "see ![](x.png) here\n"})
    res = await server.call_tool(
        "replace_in_note",
        {"path": "Embed.md", "old_text": "![](x.png)", "new_text": "![[x.png]]"},
    )
    assert result_text(res) == "Replaced 1 occurrence in Embed.md"
    assert result_text(await server.call_tool("read_note", {"path": "Embed.md"})) == "see ![[x.png]] here\n"


async def test_replace_in_note_tool_replace_all(server):
    await server.call_tool("write_note", {"path": "Embed.md", "content": "![](x.png) ![](x.png)\n"})
    res = await server.call_tool(
        "replace_in_note",
        {
            "path": "Embed.md",
            "old_text": "![](x.png)",
            "new_text": "![[x.png]]",
            "replace_all": True,
            "expected_replacements": 2,
        },
    )
    assert result_text(res) == "Replaced 2 occurrences in Embed.md"


async def test_replace_in_note_tool_missing_old_text(server):
    with pytest.raises(Exception, match="old_text not found"):
        await server.call_tool(
            "replace_in_note",
            {"path": "Inbox.md", "old_text": "absent", "new_text": "x"},
        )
```

(`pytest.raises(Exception, match=…)` is this file's established pattern for tool failures — see the existing `write_attachment` / `fetch_attachment` failure tests.)

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v -k "replace or registered"`
Expected: FAIL — unknown tool `replace_in_note`, and the registered-set assertion missing it.

- [ ] **Step 3: Implement** — in `register_tools` in `src/obsidian_mcp/tools.py`, directly after the `write_note` tool:

```python
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
            replacements would be made.
    """
    count = vault.replace_in_note(
        path, old_text, new_text, replace_all, expected_replacements
    )
    plural = "occurrence" if count == 1 else "occurrences"
    return f"Replaced {count} {plural} in {path}"
```

- [ ] **Step 4: Update README** — in the tool table add after the `write_note` row:

```markdown
| `replace_in_note(path, old_text, new_text, replace_all=false, expected_replacements=null)` | Replace exact text in a note without rewriting the rest |
```

After the table (or in the nearest examples section if one exists), add:

````markdown
### Editing a note surgically

Fix a broken image embed without rewriting the note:

```
replace_in_note(
  path="System Design Course/04 - HTTP & APIs.md",
  old_text="![](attachments/5xx-http-errors-handwritten.png)",
  new_text="![[5xx-http-errors-handwritten.png]]",
  expected_replacements=1,
)
```

`read_note(path)` returns the full markdown; `write_note(path, content,
overwrite=true)` replaces a whole note. Prefer `replace_in_note` for small
edits — it refuses to touch the file when `old_text` is missing or the
occurrence count doesn't match `expected_replacements`.
````

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/tools.py tests/test_tools.py README.md
git commit -m "feat: add replace_in_note tool"
```

---

### Task 4: End-to-end verification on the real vault

**Files:** none committed — this task only verifies. Never commit vault paths or contents.

**Interfaces:**
- Consumes: `Vault.replace_in_note` (Task 2) and the finished tool surface (Task 3).

- [ ] **Step 1: Locate the real vault path** from the MCP registration: `grep -o '"OBSIDIAN_VAULT_PATH[^,}]*' ~/.claude.json | head -3` (or ask the user). Do not write the path into any committed file.

- [ ] **Step 2: Inspect the target note** — with `VAULT=<real path>`:

```bash
OBSIDIAN_VAULT_PATH="$VAULT" uv run python - <<'EOF'
import os
from obsidian_mcp.vault import Vault
v = Vault(os.environ["OBSIDIAN_VAULT_PATH"])
content = v.read_note("System Design Course/04 - HTTP & APIs.md")
for line in content.splitlines():
    if "5xx-http-errors-handwritten" in line:
        print(repr(line))
EOF
```

Identify which broken variant is present, one of:
`![](attachments/5xx-http-errors-handwritten.png)` ·
`![[System Design Course/attachments/5xx-http-errors-handwritten.png]]` ·
`![](System Design Course/attachments/5xx-http-errors-handwritten.png)`.
If the line is already exactly `![[5xx-http-errors-handwritten.png]]`, report that and stop — nothing to fix.

- [ ] **Step 3: Fix it** — substitute the variant found as `OLD`:

```bash
OBSIDIAN_VAULT_PATH="$VAULT" OLD='<variant found>' uv run python - <<'EOF'
import os
from obsidian_mcp.vault import Vault
v = Vault(os.environ["OBSIDIAN_VAULT_PATH"])
n = v.replace_in_note(
    "System Design Course/04 - HTTP & APIs.md",
    os.environ["OLD"],
    "![[5xx-http-errors-handwritten.png]]",
    expected_replacements=1,
)
print(f"replacements: {n}")
EOF
```

- [ ] **Step 4: Verify** — re-run the Step 2 read; the matching line must be exactly `![[5xx-http-errors-handwritten.png]]` and the image file `System Design Course/attachments/5xx-http-errors-handwritten.png` must exist on disk. Report the before/after line to the user.
