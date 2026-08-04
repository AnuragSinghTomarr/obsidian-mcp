# MCP Tool Annotations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare MCP ToolAnnotations on all ten tools so client safety layers can classify calls; no behavior changes.

**Architecture:** Four shared module-level `ToolAnnotations` constants in `src/obsidian_mcp/tools.py`, referenced from each `@mcp.tool()` decorator. Nothing else changes.

**Tech Stack:** mcp 1.28.1 (`mcp.types.ToolAnnotations`), pytest (asyncio_mode auto).

**Spec:** `docs/superpowers/specs/2026-08-04-tool-annotations-design.md`

## Global Constraints

- Run everything with `uv`; full suite `uv run pytest -q` (114 tests currently; must be green before committing).
- No behavior changes — decorators gain only the `annotations=` argument.
- README updated in the same commit.
- Do NOT touch scripts/chatgpt-tunnel.sh, CLAUDE.md, or anything under docs/.

---

### Task 1: Annotations + test + README

**Files:**
- Modify: `src/obsidian_mcp/tools.py`
- Modify: `tests/test_tools.py`
- Modify: `README.md` (safety paragraph, ~line 36)

**Interfaces:**
- Produces: every registered tool has non-None `annotations` per the spec matrix.

- [ ] **Step 1: Write the failing test** — add to `tests/test_tools.py`:

```python
async def test_tool_annotations(server):
    tools = {t.name: t.annotations for t in await server.list_tools()}
    read_only = {"list_notes", "read_note", "search_notes"}
    non_destructive_writes = {"append_note", "move_note"}
    for name, ann in tools.items():
        assert ann is not None, name
        assert ann.openWorldHint is (name == "fetch_attachment"), name
        assert ann.readOnlyHint is (name in read_only), name
    for name in tools.keys() - read_only:
        assert tools[name].destructiveHint is (name not in non_destructive_writes), name
        assert tools[name].idempotentHint is False, name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_tool_annotations -v`
Expected: FAIL — `assert ann is not None` (annotations currently unset).

- [ ] **Step 3: Implement** — in `src/obsidian_mcp/tools.py`:

Add to the imports:

```python
from mcp.types import ToolAnnotations
```

Add module-level constants after `_MAGIC_BYTES` / `_check_magic`:

```python
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)
_LOCAL_WRITE_NON_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_NETWORK_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
```

Change each decorator (only the decorator line; bodies and docstrings untouched):

- `list_notes`, `read_note`, `search_notes` → `@mcp.tool(annotations=_READ_ONLY)`
- `append_note`, `move_note` → `@mcp.tool(annotations=_LOCAL_WRITE_NON_DESTRUCTIVE)`
- `write_note`, `replace_in_note`, `delete_note`, `write_attachment` → `@mcp.tool(annotations=_LOCAL_WRITE)`
- `fetch_attachment` → `@mcp.tool(annotations=_NETWORK_WRITE)`

- [ ] **Step 4: Update README** — extend the safety paragraph (after "deletion is always soft."):

```markdown
Every tool declares MCP annotations (read-only / destructive / open-world
hints) so client safety layers can classify calls; `fetch_attachment` is the
only tool marked open-world.
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: 115 passed.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/tools.py tests/test_tools.py README.md
git commit -m "feat: declare MCP tool annotations on all tools"
```
