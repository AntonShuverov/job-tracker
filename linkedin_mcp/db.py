import sqlite3
from pathlib import Path


def connect(db_path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)
