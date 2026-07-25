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
