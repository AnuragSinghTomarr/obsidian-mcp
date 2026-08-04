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
        "replace_in_note",
        "append_note",
        "search_notes",
        "move_note",
        "delete_note",
        "write_attachment",
        "fetch_attachment",
    }


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


async def test_list_notes_tool(server):
    data = json.loads(result_text(await server.call_tool("list_notes", {})))
    assert "Inbox.md" in data["notes"]


async def test_read_note_tool(server):
    res = await server.call_tool("read_note", {"path": "Inbox.md"})
    assert "capture things here" in result_text(res)


async def test_write_then_read_roundtrip(server):
    await server.call_tool("write_note", {"path": "New.md", "content": "hello\n"})
    assert result_text(await server.call_tool("read_note", {"path": "New.md"})) == "hello\n"


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


class TimeoutResponse(FakeResponse):
    """Simulates a connection that opens fine but times out mid-read."""

    def read(self, amount: int | None = None) -> bytes:
        raise TimeoutError("timed out")


async def test_fetch_attachment_reports_timeout_during_read(server, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen(TimeoutResponse(PNG_BYTES))
    )
    with pytest.raises(Exception, match="Cannot download"):
        await server.call_tool(
            "fetch_attachment",
            {"filename": "chart.png", "url": "https://example.com/chart.png"},
        )
