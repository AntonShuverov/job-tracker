from pathlib import Path

from linkedin_mcp.config import BASE_DIR, load_settings


def test_defaults(monkeypatch):
    for name in ("LINKEDIN_PROFILE_DIR", "LINKEDIN_DB_PATH", "LINKEDIN_HEADLESS",
                 "OBSIDIAN_EXPORT_PATH", "LINKEDIN_LIMIT_SEARCH",
                 "LINKEDIN_LIMIT_POST_OPEN", "LINKEDIN_LIMIT_COMMENT"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.profile_dir == BASE_DIR / "browser_profile"
    assert s.db_path == BASE_DIR / "linkedin.db"
    assert s.headless is False
    assert s.obsidian_path == Path("/Users/anton/Documents/Obsidian Vault/Jobs/LinkedIn вакансии.md")
    assert s.debug_dir == BASE_DIR / "debug"
    assert s.legacy_session == BASE_DIR / "linkedin_session.json"
    assert s.limits == {"search": 15, "post_open": 60, "comment": 8}


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("LINKEDIN_HEADLESS", "1")
    monkeypatch.setenv("LINKEDIN_LIMIT_COMMENT", "3")
    s = load_settings()
    assert s.db_path == tmp_path / "x.db"
    assert s.headless is True
    assert s.limits["comment"] == 3


def test_empty_limit_env_falls_back(monkeypatch):
    monkeypatch.setenv("LINKEDIN_LIMIT_COMMENT", "")
    monkeypatch.setenv("LINKEDIN_LIMIT_SEARCH", "")
    monkeypatch.setenv("LINKEDIN_LIMIT_POST_OPEN", "")
    s = load_settings()
    assert s.limits == {"search": 15, "post_open": 60, "comment": 8}
