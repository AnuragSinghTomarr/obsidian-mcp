# Note editing tools: `replace_in_note`, atomic writes, note size cap

**Date:** 2026-08-04
**Status:** Approved
**Branch:** feat/vault-attachments (or a new branch off it)

## Goal

Add a safe surgical-edit tool (`replace_in_note`) to the MCP server, make all
note writes atomic, and cap note size. The motivating workflow: fixing a broken
image embed in one note without rewriting the whole file.

`read_note` and `write_note` already exist and already satisfy their spec
(path confinement, `.md`-only, overwrite guard, parent-folder creation, UTF-8,
clear errors). Symlink escapes are already rejected by `Vault._resolve()`.
All nine existing tools stay — nothing is removed.

## Decisions (with rationale)

1. **Responses are human-readable strings; failures raise `VaultError`.**
   This deviates from the originating request's `{"success": true}` JSON
   envelopes, deliberately: the repo convention (CLAUDE.md) is strings for a
   model reader, and FastMCP surfaces raised exceptions as tool errors — a
   `success: false` payload a model might skim past is strictly worse.
2. **`MAX_NOTE_BYTES = 10 MB`** — a class constant in the style of
   `MAX_ATTACHMENT_BYTES`, not an env var. "Configurable" means editing the
   constant; YAGNI on runtime config.
3. **Replace-first-by-default.** With `replace_all=false` and no
   `expected_replacements`, the first occurrence is replaced even if several
   exist (per the originating request). Callers wanting ambiguity protection
   pass `expected_replacements=1`.
4. **All path validation stays in `Vault._resolve()`** — the new tool adds no
   second validation layer in `tools.py`.

## Changes

### `src/obsidian_mcp/vault.py`

- `MAX_NOTE_BYTES = 10 * 1024 * 1024`.
- `_write_text_atomic(p: Path, content: str) -> None` (private helper):
  - Enforce `MAX_NOTE_BYTES` on the UTF-8 encoded size (raise `VaultError`
    naming the path and both sizes).
  - Write to a temp file created in `p.parent` (same filesystem, so rename is
    atomic), fsync, then `os.replace(tmp, p)`.
  - If `p` already exists, copy its permission bits onto the temp file before
    the rename.
  - On any failure, remove the temp file and re-raise as `VaultError`; the
    target is never left partially written.
- `write_note()` and `append_note()` route their writes through the helper
  (behavior otherwise unchanged).
- `replace_in_note(path, old_text, new_text, replace_all=False,
  expected_replacements=None) -> int`:
  - Resolve via `_resolve()`; the note must exist.
  - `old_text` must be non-empty.
  - Count exact occurrences of `old_text`.
  - Count 0 → `VaultError` "old_text not found in <path>", file untouched.
  - `expected_replacements` given and ≠ actual planned replacement count →
    `VaultError` naming both numbers, file untouched. (Planned count = 1 when
    `replace_all` is false and occurrences ≥ 1, else the full occurrence
    count.)
  - Replace first occurrence (`str.replace(old, new, 1)`) or all
    (`str.replace(old, new)`).
  - Write atomically; return the number of replacements made.

### `src/obsidian_mcp/tools.py`

New tool, thin wrapper only:

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
    rewriting unrelated content."""
```

Returns `"Replaced <n> occurrence(s) in <path>"`.

### README

Add `replace_in_note` to the tool table and a short example for each of
`read_note`, `write_note`, `replace_in_note` (embed-fix example).

## Tests

`tests/test_vault.py` (safety + semantics):

- Replace one exact occurrence; unrelated content byte-identical.
- `replace_all=True` replaces every occurrence.
- `old_text` absent → `VaultError`, file unchanged.
- `expected_replacements` mismatch → `VaultError`, file unchanged.
- `expected_replacements` match (both modes) succeeds.
- Empty `old_text` → `VaultError`.
- Missing note → `VaultError`.
- Rejects absolute path, `../` traversal, non-`.md`, symlink escape
  (verify existing coverage; add for `replace_in_note` specifically).
- Unicode content round-trips; filenames with spaces, `&`, hyphens work.
- Note size cap: oversized `write_note` / `replace_in_note` result →
  `VaultError`, existing file unchanged.
- Atomic failure recovery: simulate a write failure (e.g. monkeypatched
  `os.replace`) → original content intact, no temp litter in the folder.

`tests/test_tools.py` (MCP surface):

- `replace_in_note` success and failure paths through the FastMCP tool,
  response-string shape, `expected_replacements` passthrough.

## End-to-end verification

Against the real vault (path taken from the running MCP server registration):
the note `System Design Course/04 - HTTP & APIs.md` must end up containing
exactly `![[5xx-http-errors-handwritten.png]]`, replacing whichever broken
variant is present (`![](attachments/…)`, `![[System Design Course/attachments/…]]`,
or `![](System Design Course/attachments/…)`). Verified by reading the note
back. No vault paths or contents are committed.

## Execution

Implementation is performed by Opus 5 subagents following TDD, task by task
from the implementation plan; the main session only reviews (diff review per
task + final branch review).
