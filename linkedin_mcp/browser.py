import asyncio
import json
import logging
import os
import re
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from . import pause
from .errors import error

log = logging.getLogger("linkedin_mcp.browser")

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"
LOCK_MARKERS = ("processsingleton", "singletonlock", "profile is already in use", "user data directory is already in use")
LOGGED_OUT_PATH_RE = re.compile(r"^https?://[^/]+/(authwall|login|uas/login|checkpoint|signup)(?:[/?#]|$)", re.IGNORECASE)


class ProfileLockedError(RuntimeError):
    pass


class NotLoggedInError(RuntimeError):
    pass


def is_profile_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in LOCK_MARKERS)


def is_logged_out_url(url: str) -> bool:
    return bool(LOGGED_OUT_PATH_RE.match(url))


class LinkedInBrowser:
    def __init__(self, profile_dir: Path, headless: bool, legacy_session: Path | None = None):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.legacy_session = legacy_session
        self.lock = asyncio.Lock()
        self.notice: str | None = None
        self._pw = None
        self._ctx = None
        self._page = None
        self._running_headless: bool | None = None
        self._checked = False

    async def _start(self, headless: bool) -> None:
        fresh = not self.profile_dir.exists() or not any(self.profile_dir.iterdir())
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.profile_dir, 0o700)
        self._pw = await async_playwright().start()
        try:
            self._ctx = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=headless,
                viewport={"width": 1366, "height": 850},
                locale="ru-RU",
                timezone_id="Asia/Almaty",
            )
        except Exception as e:
            await self._pw.stop()
            self._pw = None
            if is_profile_lock_error(e):
                raise ProfileLockedError(
                    f"Профиль {self.profile_dir} открыт другим процессом браузера. Закрой его и повтори.") from e
            raise
        self._running_headless = headless
        if fresh and self.legacy_session and self.legacy_session.exists():
            cookies = json.loads(self.legacy_session.read_text()).get("cookies", [])
            if cookies:
                await self._ctx.add_cookies(cookies)
                self.notice = (f"Куки импортированы из {self.legacy_session.name} в профиль браузера. "
                               f"Если вход работает, удали этот файл.")
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

    async def page(self, headed: bool = False) -> "Page":
        want_headless = self.headless and not headed
        if self._page is not None and not self._page.is_closed():
            if not headed or self._running_headless == want_headless:
                return self._page
            await self.close()
        await self._start(want_headless)
        return self._page

    async def _ensure_logged_in(self, page) -> None:
        await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=45000)
        await pause.human_pause(2, 4)
        if is_logged_out_url(page.url):
            raise NotLoggedInError("LinkedIn не авторизован (или требует проверку). Вызови linkedin_login.")
        self._checked = True

    async def goto(self, url: str):
        page = await self.page()
        if not self._checked:
            await self._ensure_logged_in(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if is_logged_out_url(page.url):
            self._checked = False
            raise NotLoggedInError("LinkedIn разлогинил сессию или показал проверку. Вызови linkedin_login.")
        return page

    async def login(self, timeout_s: int = 300) -> dict:
        page = await self.page(headed=True)
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        if "/feed" in page.url:
            self._checked = True
            return {"ok": True, "message": "Вход уже выполнен."}
        try:
            await page.wait_for_url(re.compile(r"linkedin\.com/feed"), timeout=timeout_s * 1000)
        except PlaywrightTimeoutError:
            return error("login_timeout", f"Вход не завершён за {timeout_s} с. Повтори linkedin_login.")
        self._checked = True
        return {"ok": True, "message": "Вход выполнен, сессия сохранена в профиле браузера."}

    async def close(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.close()
            except Exception as e:
                log.warning("close context: %s", e)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as e:
                log.warning("stop playwright: %s", e)
        self._pw = self._ctx = self._page = None
        self._running_headless = None
        self._checked = False
