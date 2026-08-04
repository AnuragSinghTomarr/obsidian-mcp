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

    def test_tolerates_non_utf8_file(self, vault, vault_dir):
        (vault_dir / "bad.md").write_bytes(b"\xff\xfe garbage")
        paths = {m["path"] for m in vault.search_notes("inverter")}
        assert paths == {"Daily/2026-07-24.md", "Projects/Solar/Deye.md"}


class TestSymlinkConfinement:
    def test_list_notes_skips_escaping_symlink(self, vault, vault_dir):
        outside = vault_dir.parent / "outside_secret"
        outside.mkdir()
        (outside / "secret.md").write_text("topsecret leak\n", encoding="utf-8")
        (vault_dir / "link.md").symlink_to(outside / "secret.md")
        assert "link.md" not in vault.list_notes()["notes"]

    def test_search_ignores_escaping_symlink(self, vault, vault_dir):
        outside = vault_dir.parent / "outside_secret2"
        outside.mkdir()
        (outside / "secret.md").write_text("topsecret leak\n", encoding="utf-8")
        (vault_dir / "link.md").symlink_to(outside / "secret.md")
        assert vault.search_notes("topsecret") == []


class TestOSErrorWrapping:
    def test_write_note_parent_is_file(self, vault, vault_dir):
        with pytest.raises(VaultError) as exc:
            vault.write_note("Inbox.md/sub.md", "x")
        assert str(vault_dir) not in str(exc.value)

    def test_move_note_parent_is_file(self, vault, vault_dir):
        with pytest.raises(VaultError) as exc:
            vault.move_note("Daily/2026-07-24.md", "Inbox.md/sub.md")
        assert str(vault_dir) not in str(exc.value)


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
