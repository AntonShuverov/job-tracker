from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from linkedin_mcp import db, pause

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    async def _noop(*args, **kwargs):
        return None
    monkeypatch.setattr(pause, "human_pause", _noop)
    monkeypatch.setattr(pause, "sleep", _noop)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest_asyncio.fixture
async def page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        yield pg
        await browser.close()
