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
