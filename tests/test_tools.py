import base64
import json

import pytest

from obsidian_mcp.server import build_server


@pytest.fixture
def server(vault_dir):
    return build_server(vault_dir)


def result_text(res) -> str:
    content = res[0] if isinstance(res, tuple) else res
    return "\n".join(b.text for b in content if hasattr(b, "text"))


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
