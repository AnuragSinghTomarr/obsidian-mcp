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
