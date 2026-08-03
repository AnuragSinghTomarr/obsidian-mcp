# Vault Image Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the model place an image file into the Obsidian vault and hand back the `![[…]]` wikilink, so it stops telling the user to save generated images by hand.

**Architecture:** Two new primitives on the existing `Vault` class (`_resolve_attachment`, `write_attachment`) plus a config reader (`attachment_folder`), then two new FastMCP tools in `tools.py` that differ only in how bytes arrive: `write_attachment` decodes base64, `fetch_attachment` downloads from an http(s) URL using the standard library. Neither tool touches notes — they return the embed string for the caller to pass to the existing `append_note`.

**Tech Stack:** Python ≥ 3.11, `mcp>=1.2.0` (FastMCP), stdlib `base64` / `urllib.request` / `json`, pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-08-03-vault-attachments-design.md`

## Global Constraints

- No new third-party dependencies. `pyproject.toml` stays at `dependencies = ["mcp>=1.2.0"]`; the download path uses stdlib `urllib.request`.
- Every failure the caller can fix raises `VaultError` with a message that names the offending input. No bare `except`, no silent fallback except the documented `attachment_folder()` default.
- Allowed suffixes, case-insensitive: `.png .jpg .jpeg .gif .webp .svg .bmp`.
- Size cap: `MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024`.
- Default attachment folder when Obsidian's setting is unusable: `"attachments"`.
- All attachment paths go through the existing `Vault._resolve`, which is **not** modified — it already blocks absolute paths, `../` escapes, and hidden (dot) path components.
- Tools never mutate notes. They return JSON `{"path", "embed", "bytes"}`.
- `filename` arguments are bare filenames; a `/` or `\` in them is an error.
- Run tests with `uv run pytest` from the repo root.
- Match the existing terse code style: `from __future__ import annotations` at the top of each module, docstrings with an `Args:` block on every tool.

---

### Task 1: Vault attachment write primitives

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (add class constants near `MAX_MATCHES` on line 11; add `_resolve_attachment` after `_resolve` which ends at line 30; add `write_attachment` after `write_note` which ends at line 69)
- Test: `tests/test_vault.py` (append new test classes at the end, after `TestSearchNotes`)

**Interfaces:**
- Consumes: existing `Vault._resolve(rel, *, require_md=False) -> Path` and `VaultError`.
- Produces:
  - `Vault.IMAGE_SUFFIXES: set[str]`
  - `Vault.MAX_ATTACHMENT_BYTES: int`
  - `Vault._resolve_attachment(rel: str) -> Path`
  - `Vault.write_attachment(path: str, data: bytes, overwrite: bool = False) -> str` returning the vault-relative POSIX path.

**Context:** `tests/conftest.py` already provides `vault_dir` (a `tmp_path` vault containing `Inbox.md`, `Daily/`, `Projects/Solar/`, `.obsidian/app.json` holding `{}`, and a root `diagram.png`) and `vault` (a `Vault` built on it). Reuse those fixtures — do not write new ones.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault.py`:

```python
class TestResolveAttachment:
    def test_accepts_image_suffix(self, vault, vault_dir):
        assert vault._resolve_attachment("attachments/x.png") == (
            vault_dir / "attachments" / "x.png"
        )

    def test_suffix_check_is_case_insensitive(self, vault, vault_dir):
        assert vault._resolve_attachment("X.PNG") == vault_dir / "X.PNG"

    def test_rejects_non_image_suffix(self, vault):
        with pytest.raises(VaultError, match="Only image attachments"):
            vault._resolve_attachment("notes.txt")
        with pytest.raises(VaultError, match="Only image attachments"):
            vault._resolve_attachment("evil.md")

    def test_rejects_suffixless_path(self, vault):
        with pytest.raises(VaultError, match="Only image attachments"):
            vault._resolve_attachment("Daily")

    def test_rejects_traversal(self, vault):
        with pytest.raises(VaultError, match="escapes the vault"):
            vault._resolve_attachment("../outside.png")

    def test_rejects_absolute_path(self, vault):
        with pytest.raises(VaultError, match="Absolute paths"):
            vault._resolve_attachment("/tmp/evil.png")

    def test_rejects_hidden_parts(self, vault):
        with pytest.raises(VaultError, match="Hidden"):
            vault._resolve_attachment(".obsidian/logo.png")


class TestWriteAttachment:
    def test_creates_nested_folder(self, vault, vault_dir):
        saved = vault.write_attachment("assets/img/x.png", b"\x89PNG bytes")
        assert saved == "assets/img/x.png"
        assert (vault_dir / "assets" / "img" / "x.png").read_bytes() == b"\x89PNG bytes"

    def test_refuses_existing_without_overwrite(self, vault):
        with pytest.raises(VaultError, match="already exists"):
            vault.write_attachment("diagram.png", b"new bytes")

    def test_overwrites_when_allowed(self, vault, vault_dir):
        vault.write_attachment("diagram.png", b"new bytes", overwrite=True)
        assert (vault_dir / "diagram.png").read_bytes() == b"new bytes"

    def test_rejects_empty_data(self, vault):
        with pytest.raises(VaultError, match="empty"):
            vault.write_attachment("x.png", b"")

    def test_rejects_oversized_data(self, vault):
        oversized = b"\x00" * (Vault.MAX_ATTACHMENT_BYTES + 1)
        with pytest.raises(VaultError, match="over the"):
            vault.write_attachment("x.png", oversized)

    def test_rejects_non_image_suffix(self, vault):
        with pytest.raises(VaultError, match="Only image attachments"):
            vault.write_attachment("x.txt", b"data")

    def test_parent_is_an_existing_file(self, vault):
        with pytest.raises(VaultError, match="Cannot write"):
            vault.write_attachment("Inbox.md/x.png", b"data")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -k "Attachment" -v`
Expected: FAIL — `AttributeError: 'Vault' object has no attribute '_resolve_attachment'`.

- [ ] **Step 3: Add the constants**

In `src/obsidian_mcp/vault.py`, alongside the existing `MAX_MATCHES = 50` inside `class Vault`:

```python
class Vault:
    MAX_MATCHES = 50
    MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
```

- [ ] **Step 4: Add `_resolve_attachment` directly after `_resolve`**

```python
    def _resolve_attachment(self, rel: str) -> Path:
        candidate = self._resolve(rel, require_md=False)
        if candidate.suffix.lower() not in self.IMAGE_SUFFIXES:
            allowed = ", ".join(sorted(self.IMAGE_SUFFIXES))
            raise VaultError(f"Only image attachments are supported ({allowed}): {rel}")
        return candidate
```

- [ ] **Step 5: Add `write_attachment` directly after `write_note`**

```python
    def write_attachment(self, path: str, data: bytes, overwrite: bool = False) -> str:
        if not data:
            raise VaultError(f"Attachment data is empty: {path}")
        if len(data) > self.MAX_ATTACHMENT_BYTES:
            raise VaultError(
                f"Attachment is {len(data)} bytes, over the "
                f"{self.MAX_ATTACHMENT_BYTES} byte limit: {path}"
            )
        p = self._resolve_attachment(path)
        if p.exists() and not overwrite:
            raise VaultError(
                f"Attachment already exists (pass overwrite=true to replace): {path}"
            )
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        except OSError:
            raise VaultError(
                f"Cannot write {path}: a parent path component is an existing file"
            )
        return p.relative_to(self.root).as_posix()
```

Note the ordering: size and emptiness are checked before resolution so an oversized payload never touches the filesystem.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: PASS — the new classes plus all pre-existing vault tests.

- [ ] **Step 7: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: add vault image attachment write primitives"
```

---

### Task 2: Attachment folder detection

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (add `import json` to the imports; add `DEFAULT_ATTACHMENT_FOLDER` to the class constants; add `attachment_folder` after `_resolve_attachment`)
- Test: `tests/test_vault.py` (append a new test class at the end)

**Interfaces:**
- Consumes: `Vault.root`.
- Produces:
  - `Vault.DEFAULT_ATTACHMENT_FOLDER: str` = `"attachments"`
  - `Vault.attachment_folder() -> str` — a vault-relative folder path, never absolute, never `./`-prefixed, possibly `"attachments"`.

**Why:** Obsidian stores the user's attachment folder in `<vault>/.obsidian/app.json` under `attachmentFolderPath`. Without reading it the model guesses the folder wrong, which is the original complaint. `.obsidian/` is deliberately invisible to `_resolve`, so this method reads the file directly rather than going through path resolution — it only ever reads, never writes there.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault.py`:

```python
class TestAttachmentFolder:
    def _configure(self, vault_dir, raw: str) -> None:
        (vault_dir / ".obsidian" / "app.json").write_text(raw, encoding="utf-8")

    def test_uses_configured_value(self, vault, vault_dir):
        self._configure(vault_dir, '{"attachmentFolderPath": "Media/Images"}')
        assert vault.attachment_folder() == "Media/Images"

    def test_strips_leading_slash(self, vault, vault_dir):
        self._configure(vault_dir, '{"attachmentFolderPath": "/Media"}')
        assert vault.attachment_folder() == "Media"

    def test_default_when_key_missing(self, vault):
        # conftest writes "{}" into .obsidian/app.json
        assert vault.attachment_folder() == "attachments"

    def test_default_when_value_empty(self, vault, vault_dir):
        self._configure(vault_dir, '{"attachmentFolderPath": ""}')
        assert vault.attachment_folder() == "attachments"

    def test_default_when_value_is_note_relative(self, vault, vault_dir):
        self._configure(vault_dir, '{"attachmentFolderPath": "./assets"}')
        assert vault.attachment_folder() == "attachments"

    def test_default_when_value_is_root_slash(self, vault, vault_dir):
        self._configure(vault_dir, '{"attachmentFolderPath": "/"}')
        assert vault.attachment_folder() == "attachments"

    def test_default_when_value_wrong_type(self, vault, vault_dir):
        self._configure(vault_dir, '{"attachmentFolderPath": 42}')
        assert vault.attachment_folder() == "attachments"

    def test_default_when_json_malformed(self, vault, vault_dir):
        self._configure(vault_dir, "{not json")
        assert vault.attachment_folder() == "attachments"

    def test_default_when_json_is_not_an_object(self, vault, vault_dir):
        self._configure(vault_dir, "[1, 2, 3]")
        assert vault.attachment_folder() == "attachments"

    def test_default_when_config_absent(self, vault, vault_dir):
        (vault_dir / ".obsidian" / "app.json").unlink()
        assert vault.attachment_folder() == "attachments"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -k AttachmentFolder -v`
Expected: FAIL — `AttributeError: 'Vault' object has no attribute 'attachment_folder'`.

- [ ] **Step 3: Add the import and the constant**

`src/obsidian_mcp/vault.py` already imports `Path`; add `json` above it so the block reads:

```python
from __future__ import annotations

import json
from pathlib import Path
```

And add to the class constants from Task 1:

```python
    DEFAULT_ATTACHMENT_FOLDER = "attachments"
```

- [ ] **Step 4: Add `attachment_folder` after `_resolve_attachment`**

```python
    def attachment_folder(self) -> str:
        """The vault's configured attachment folder, or a sane default.

        Reads Obsidian's own `attachmentFolderPath` setting. Falls back to
        DEFAULT_ATTACHMENT_FOLDER when the config is absent, unreadable,
        malformed, empty (Obsidian's "vault root"), or note-relative ("./x"),
        which cannot be resolved without knowing the target note.
        """
        config = self.root / ".obsidian" / "app.json"
        try:
            settings = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self.DEFAULT_ATTACHMENT_FOLDER
        if not isinstance(settings, dict):
            return self.DEFAULT_ATTACHMENT_FOLDER
        configured = settings.get("attachmentFolderPath")
        if not isinstance(configured, str):
            return self.DEFAULT_ATTACHMENT_FOLDER
        configured = configured.strip().strip("/")
        if not configured or configured.startswith("./"):
            return self.DEFAULT_ATTACHMENT_FOLDER
        return configured
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "feat: read Obsidian's configured attachment folder"
```

---

### Task 3: `write_attachment` MCP tool (base64)

**Files:**
- Modify: `src/obsidian_mcp/tools.py` (add imports; add two module-level helpers above `register_tools`; add the tool inside `register_tools` after `write_note`, which ends at line 40)
- Test: `tests/test_tools.py` (update the registered-tools test at lines 18-28; append new tests)

**Interfaces:**
- Consumes: `Vault.write_attachment`, `Vault.attachment_folder`, `Vault.MAX_ATTACHMENT_BYTES`, `VaultError` (all from Tasks 1-2).
- Produces:
  - module-level `_attachment_result(saved: str, size: int) -> str` in `tools.py`
  - closure `_attachment_path(filename: str, folder: str) -> str` inside `register_tools`
  - MCP tool `write_attachment(filename: str, base64_data: str, folder: str = "", overwrite: bool = False) -> str`

`_attachment_path` is a closure rather than a module function because it needs `vault.attachment_folder()`. Task 4 reuses both helpers unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tools.py`, replace the existing `test_all_seven_tools_registered` (lines 18-28) with the eight-tool set. Task 4 extends this same test to nine; asserting eight here keeps this task's commit green.

```python
async def test_all_registered_tools(server):
    names = {t.name for t in await server.list_tools()}
    assert names == {
        "list_notes",
        "read_note",
        "write_note",
        "append_note",
        "search_notes",
        "move_note",
        "delete_note",
        "write_attachment",
    }
```

Then append (this file has no `Path` import yet — add `import base64` and `from pathlib import Path` to its imports):

```python
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"pretend pixels"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()


async def test_write_attachment_saves_to_default_folder(server, vault_dir):
    res = await server.call_tool(
        "write_attachment", {"filename": "chart.png", "base64_data": PNG_B64}
    )
    data = json.loads(result_text(res))
    assert data == {
        "path": "attachments/chart.png",
        "embed": "![[chart.png]]",
        "bytes": len(PNG_BYTES),
    }
    assert (vault_dir / "attachments" / "chart.png").read_bytes() == PNG_BYTES


async def test_write_attachment_honours_explicit_folder(server, vault_dir):
    await server.call_tool(
        "write_attachment",
        {"filename": "chart.png", "base64_data": PNG_B64, "folder": "Media/Img"},
    )
    assert (vault_dir / "Media" / "Img" / "chart.png").is_file()


async def test_write_attachment_honours_configured_folder(server, vault_dir):
    (vault_dir / ".obsidian" / "app.json").write_text(
        '{"attachmentFolderPath": "Files"}', encoding="utf-8"
    )
    res = await server.call_tool(
        "write_attachment", {"filename": "chart.png", "base64_data": PNG_B64}
    )
    assert json.loads(result_text(res))["path"] == "Files/chart.png"


async def test_write_attachment_accepts_data_uri_prefix(server, vault_dir):
    await server.call_tool(
        "write_attachment",
        {"filename": "chart.png", "base64_data": f"data:image/png;base64,{PNG_B64}"},
    )
    assert (vault_dir / "attachments" / "chart.png").read_bytes() == PNG_BYTES


async def test_write_attachment_accepts_wrapped_base64(server, vault_dir):
    wrapped = "\n".join([PNG_B64[:8], PNG_B64[8:]])
    await server.call_tool(
        "write_attachment", {"filename": "chart.png", "base64_data": wrapped}
    )
    assert (vault_dir / "attachments" / "chart.png").read_bytes() == PNG_BYTES


async def test_write_attachment_rejects_bad_base64(server):
    with pytest.raises(Exception, match="not valid base64"):
        await server.call_tool(
            "write_attachment", {"filename": "chart.png", "base64_data": "!!!not base64!!!"}
        )


async def test_write_attachment_rejects_path_in_filename(server):
    with pytest.raises(Exception, match="bare filename"):
        await server.call_tool(
            "write_attachment",
            {"filename": "sub/chart.png", "base64_data": PNG_B64},
        )


async def test_write_attachment_rejects_non_image(server):
    with pytest.raises(Exception, match="Only image attachments"):
        await server.call_tool(
            "write_attachment", {"filename": "notes.txt", "base64_data": PNG_B64}
        )


async def test_write_attachment_refuses_existing(server):
    args = {"filename": "chart.png", "base64_data": PNG_B64}
    await server.call_tool("write_attachment", args)
    with pytest.raises(Exception, match="already exists"):
        await server.call_tool("write_attachment", args)
    await server.call_tool("write_attachment", {**args, "overwrite": True})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `test_all_registered_tools` shows `write_attachment` missing, and the `write_attachment` calls raise "Unknown tool".

- [ ] **Step 3: Add imports and the result helper to `tools.py`**

Replace the current import block in `src/obsidian_mcp/tools.py` (lines 1-7) with:

```python
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from obsidian_mcp.vault import Vault, VaultError
```

Then, above `register_tools`, add:

```python
def _attachment_result(saved: str, size: int) -> str:
    """JSON for a saved attachment: its path, a ready-to-paste embed, its size."""
    return json.dumps(
        {"path": saved, "embed": f"![[{Path(saved).name}]]", "bytes": size},
        indent=2,
    )
```

The embed uses the bare filename because Obsidian resolves wikilinks across the whole vault.

- [ ] **Step 4: Add the path helper and the tool inside `register_tools`**

Immediately inside `register_tools`, before the first `@mcp.tool()`:

```python
    def _attachment_path(filename: str, folder: str) -> str:
        if "/" in filename or "\\" in filename:
            raise VaultError(
                f"filename must be a bare filename, not a path (use folder=): {filename}"
            )
        base = folder.strip().strip("/") if folder else vault.attachment_folder()
        return f"{base}/{filename}" if base else filename
```

Then after the existing `write_note` tool:

```python
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
```

Whitespace is stripped before decoding because `validate=True` rejects the line breaks models commonly emit.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS, every test including `test_all_registered_tools`. Do not commit until it is fully green.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/tools.py tests/test_tools.py
git commit -m "feat: add write_attachment tool for base64 images"
```

---

### Task 4: `fetch_attachment` MCP tool (URL download) and docs

**Files:**
- Modify: `src/obsidian_mcp/tools.py` (add `urllib` imports; add `_MAGIC_BYTES` and `_check_magic` module-level; add the tool after `write_attachment`)
- Modify: `README.md` (tool table at lines 24-32; safety paragraph at lines 34-35)
- Test: `tests/test_tools.py` (append)

**Interfaces:**
- Consumes: `_attachment_result`, `_attachment_path`, `Vault.write_attachment`, `Vault.MAX_ATTACHMENT_BYTES` (Tasks 1-3).
- Produces: MCP tool `fetch_attachment(filename: str, url: str, folder: str = "", overwrite: bool = False) -> str`, same JSON return shape as `write_attachment`.

**Why this tool is the real fix:** a 500 KB PNG is roughly 680 K base64 characters, past any model's single-response output limit. Downloading server-side is the only path that works for a generated image of real size.

- [ ] **Step 1: Write the failing tests**

First add `"fetch_attachment"` to the expected set in `test_all_registered_tools` (Task 3 left it at eight names), so it now asserts all nine.

Then append to `tests/test_tools.py`:

```python
class FakeResponse:
    """Minimal stand-in for the http.client.HTTPResponse urlopen returns."""

    def __init__(self, data: bytes, url: str = "https://example.com/chart.png"):
        self._data = data
        self.url = url

    def read(self, amount: int | None = None) -> bytes:
        return self._data if amount is None else self._data[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(response):
    def opener(url, timeout=None):
        return response

    return opener


async def test_fetch_attachment_downloads_image(server, vault_dir, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(FakeResponse(PNG_BYTES)))
    res = await server.call_tool(
        "fetch_attachment",
        {"filename": "chart.png", "url": "https://example.com/chart.png"},
    )
    data = json.loads(result_text(res))
    assert data == {
        "path": "attachments/chart.png",
        "embed": "![[chart.png]]",
        "bytes": len(PNG_BYTES),
    }
    assert (vault_dir / "attachments" / "chart.png").read_bytes() == PNG_BYTES


async def test_fetch_attachment_rejects_non_http_scheme(server):
    with pytest.raises(Exception, match="Only http and https"):
        await server.call_tool(
            "fetch_attachment",
            {"filename": "chart.png", "url": "file:///etc/passwd"},
        )


async def test_fetch_attachment_rejects_redirect_off_http(server, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        fake_urlopen(FakeResponse(PNG_BYTES, url="file:///etc/passwd")),
    )
    with pytest.raises(Exception, match="redirected to an unsupported scheme"):
        await server.call_tool(
            "fetch_attachment",
            {"filename": "chart.png", "url": "https://example.com/chart.png"},
        )


async def test_fetch_attachment_rejects_oversized_download(server, monkeypatch):
    from obsidian_mcp.vault import Vault

    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * Vault.MAX_ATTACHMENT_BYTES
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(FakeResponse(oversized)))
    with pytest.raises(Exception, match="exceeds the"):
        await server.call_tool(
            "fetch_attachment",
            {"filename": "chart.png", "url": "https://example.com/chart.png"},
        )


async def test_fetch_attachment_rejects_html_error_page(server, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        fake_urlopen(FakeResponse(b"<!doctype html><title>404</title>")),
    )
    with pytest.raises(Exception, match="not a valid .png image"):
        await server.call_tool(
            "fetch_attachment",
            {"filename": "chart.png", "url": "https://example.com/chart.png"},
        )


async def test_fetch_attachment_accepts_svg_without_magic_bytes(server, vault_dir, monkeypatch):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(FakeResponse(svg)))
    await server.call_tool(
        "fetch_attachment",
        {"filename": "logo.svg", "url": "https://example.com/logo.svg"},
    )
    assert (vault_dir / "attachments" / "logo.svg").read_bytes() == svg


async def test_fetch_attachment_reports_network_failure(server, monkeypatch):
    import urllib.error

    def boom(url, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(Exception, match="Cannot download"):
        await server.call_tool(
            "fetch_attachment",
            {"filename": "chart.png", "url": "https://example.com/chart.png"},
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `test_all_registered_tools` shows `fetch_attachment` missing, and every `fetch_attachment` call raises "Unknown tool".

- [ ] **Step 3: Add the urllib imports and the magic-byte check to `tools.py`**

Extend the import block from Task 3 with:

```python
import urllib.error
import urllib.request
from urllib.parse import urlparse
```

Then add above `register_tools`, next to `_attachment_result`:

```python
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
```

- [ ] **Step 4: Add the tool after `write_attachment`**

```python
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
        except urllib.error.URLError as exc:
            raise VaultError(f"Cannot download {url}: {exc.reason}")
        if len(data) > limit:
            raise VaultError(f"Download exceeds the {limit} byte limit: {url}")
        _check_magic(filename, data)
        saved = vault.write_attachment(target, data, overwrite)
        return _attachment_result(saved, len(data))
```

One bounded `read(limit + 1)` replaces the chunked loop the spec sketched: it caps memory and disk identically with less code, and one extra byte is enough to detect "over the limit".

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS — including `test_all_registered_tools`, now asserting all nine.

- [ ] **Step 6: Update the README tool table**

In `README.md`, add two rows after the `delete_note` row (line 32):

```markdown
| `write_attachment(filename, base64_data, folder="", overwrite=false)` | Save a base64 image; returns its path and `![[embed]]` |
| `fetch_attachment(filename, url, folder="", overwrite=false)` | Download an image from an http(s) URL into the vault |
```

- [ ] **Step 7: Extend the README safety paragraph**

Replace the existing paragraph (lines 34-35):

```markdown
Safety: all paths are confined to the vault (no `../`, no absolute paths),
dot-folders like `.obsidian/` are invisible, and deletion is always soft.

Attachments: images only (`.png .jpg .jpeg .gif .webp .svg .bmp`), capped at
25 MB, written to the folder Obsidian is configured to use
(`attachmentFolderPath` in `.obsidian/app.json`, defaulting to `attachments/`).
`fetch_attachment` makes the server perform an outbound `GET` on whatever
http(s) URL it is given — the only tool here that touches the network. Downloads
must match their claimed image type, so an error page is rejected rather than
saved, but note that a prompt-injected instruction could still aim that `GET` at
a host reachable from this machine.
```

- [ ] **Step 8: Verify the whole suite once more**

Run: `uv run pytest`
Expected: PASS, no failures, no errors.

- [ ] **Step 9: Commit**

```bash
git add src/obsidian_mcp/tools.py tests/test_tools.py README.md
git commit -m "feat: add fetch_attachment tool and document attachment safety"
```

---

## Manual verification

After Task 4, confirm the original failure is gone end-to-end:

1. `uv run obsidian-mcp --vault "$HOME/Documents/MyVault"` starts without error.
2. In Claude Code, `write_attachment` with a tiny base64 PNG lands the file in the vault's attachment folder and Obsidian shows it in a note using the returned embed string.
3. `fetch_attachment` with a real image URL saves the file and returns matching `bytes`.
