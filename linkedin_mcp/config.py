import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_OBSIDIAN_PATH = "/Users/anton/Documents/Obsidian Vault/Jobs/LinkedIn вакансии.md"


@dataclass(frozen=True)
class Settings:
    profile_dir: Path
    db_path: Path
    headless: bool
    obsidian_path: Path
    debug_dir: Path
    legacy_session: Path
    limits: dict[str, int]


def load_settings() -> Settings:
    env = os.getenv
    return Settings(
        profile_dir=Path(env("LINKEDIN_PROFILE_DIR") or BASE_DIR / "browser_profile"),
        db_path=Path(env("LINKEDIN_DB_PATH") or BASE_DIR / "linkedin.db"),
        headless=env("LINKEDIN_HEADLESS", "0") == "1",
        obsidian_path=Path(env("OBSIDIAN_EXPORT_PATH") or DEFAULT_OBSIDIAN_PATH),
        debug_dir=BASE_DIR / "debug",
        legacy_session=BASE_DIR / "linkedin_session.json",
        limits={
            "search": int(env("LINKEDIN_LIMIT_SEARCH") or "30"),
            "post_open": int(env("LINKEDIN_LIMIT_POST_OPEN") or "60"),
            "comment": int(env("LINKEDIN_LIMIT_COMMENT") or "8"),
            "feed": int(env("LINKEDIN_LIMIT_FEED") or "5"),
        },
    )
