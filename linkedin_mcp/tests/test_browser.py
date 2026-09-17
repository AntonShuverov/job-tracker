import json
import stat

from linkedin_mcp.browser import LinkedInBrowser, NotLoggedInError, is_logged_out_url, is_profile_lock_error


def test_is_logged_out_url():
    assert is_logged_out_url("https://www.linkedin.com/authwall?trk=x")
    assert is_logged_out_url("https://www.linkedin.com/login")
    assert is_logged_out_url("https://www.linkedin.com/checkpoint/challenge/123")
    assert is_logged_out_url("https://www.linkedin.com/uas/login?session_redirect=x")
    assert not is_logged_out_url("https://www.linkedin.com/feed/")
    assert not is_logged_out_url("https://www.linkedin.com/search/results/content/?keywords=login")
    assert not is_logged_out_url("https://www.linkedin.com/loginhelp")
    assert not is_logged_out_url("https://www.linkedin.com/checkpointer")
    assert is_logged_out_url("https://www.linkedin.com/login?x=1")
    assert is_logged_out_url("https://www.linkedin.com/checkpoint/challenge/1")


def test_is_profile_lock_error():
    assert is_profile_lock_error(Exception("Failed to create a ProcessSingleton for your profile directory"))
    assert is_profile_lock_error(Exception("... SingletonLock: File exists"))
    assert not is_profile_lock_error(Exception("net::ERR_NAME_NOT_RESOLVED"))


async def test_legacy_cookie_migration(tmp_path):
    legacy = tmp_path / "linkedin_session.json"
    legacy.write_text(json.dumps({"cookies": [{
        "name": "li_at", "value": "secret", "domain": ".linkedin.com", "path": "/",
        "expires": -1, "httpOnly": True, "secure": True, "sameSite": "None"}], "origins": []}))
    profile = tmp_path / "profile"
    b = LinkedInBrowser(profile, headless=True, legacy_session=legacy)
    try:
        await b.page()
        cookies = await b._ctx.cookies("https://www.linkedin.com")
        assert any(c["name"] == "li_at" and c["value"] == "secret" for c in cookies)
        assert "linkedin_session.json" in b.notice
        assert stat.S_IMODE(profile.stat().st_mode) == 0o700
    finally:
        await b.close()

    b2 = LinkedInBrowser(profile, headless=True, legacy_session=legacy)
    try:
        await b2.page()
        assert b2.notice is None
    finally:
        await b2.close()


async def test_goto_raises_when_logged_out(tmp_path):
    profile = tmp_path / "profile"
    b = LinkedInBrowser(profile, headless=True)
    try:
        await b.page()

        async def handler(route):
            url = route.request.url
            if url == "https://www.linkedin.com/feed/":
                await route.fulfill(status=302, headers={"Location": "https://www.linkedin.com/authwall?trk=x"})
            elif "authwall" in url:
                await route.fulfill(status=200, content_type="text/html", body="<html>authwall</html>")
            else:
                await route.fulfill(status=200, content_type="text/html", body="<html>ok</html>")

        await b._ctx.route("**/*", handler)

        try:
            await b.goto("https://www.linkedin.com/search/results/content/?keywords=x")
            assert False, "expected NotLoggedInError"
        except NotLoggedInError:
            pass
    finally:
        await b.close()


async def test_goto_ok_when_logged_in(tmp_path):
    profile = tmp_path / "profile"
    b = LinkedInBrowser(profile, headless=True)
    try:
        await b.page()
        requested = []

        async def handler(route):
            requested.append(route.request.url)
            await route.fulfill(status=200, content_type="text/html", body="<html>ok</html>")

        await b._ctx.route("**/*", handler)

        target = "https://www.linkedin.com/search/results/content/?keywords=x"
        page = await b.goto(target)
        assert page.url == target

        page2 = await b.goto(target)
        assert page2.url == target

        feed_hits = [u for u in requested if u == "https://www.linkedin.com/feed/"]
        assert len(feed_hits) == 1
    finally:
        await b.close()


async def test_goto_checkpoint_after_navigation(tmp_path):
    profile = tmp_path / "profile"
    b = LinkedInBrowser(profile, headless=True)
    try:
        await b.page()
        target = "https://www.linkedin.com/search/results/content/?keywords=x"

        async def handler(route):
            url = route.request.url
            if url == "https://www.linkedin.com/feed/":
                await route.fulfill(status=200, content_type="text/html", body="<html>feed</html>")
            elif url == target:
                await route.fulfill(status=302, headers={"Location": "https://www.linkedin.com/checkpoint/challenge/1"})
            else:
                await route.fulfill(status=200, content_type="text/html", body="<html>ok</html>")

        await b._ctx.route("**/*", handler)

        try:
            await b.goto(target)
            assert False, "expected NotLoggedInError"
        except NotLoggedInError:
            pass
    finally:
        await b.close()


async def test_login_timeout(tmp_path):
    profile = tmp_path / "profile"
    b = LinkedInBrowser(profile, headless=True)
    orig_start = b._start

    async def patched_start(headless):
        # Force a headless launch even when login() requests a headed page
        # (headed=True), so the test never opens a real window.
        await orig_start(True)

        async def handler(route):
            await route.fulfill(status=200, content_type="text/html", body="<html>login</html>")

        await b._ctx.route("**/*", handler)

    b._start = patched_start
    try:
        result = await b.login(timeout_s=1)
        assert result.get("error") == "login_timeout"
    finally:
        await b.close()


async def test_login_already_logged_in(tmp_path):
    profile = tmp_path / "profile"
    b = LinkedInBrowser(profile, headless=True)
    orig_start = b._start

    async def patched_start(headless):
        # Force a headless launch even when login() requests a headed page.
        await orig_start(True)

        async def handler(route):
            url = route.request.url
            if url == "https://www.linkedin.com/login":
                # A raw 302 (route.fulfill(status=302, headers={"Location": ...}))
                # is NOT honored by Chromium for a top-level navigation to this
                # specific path in this Playwright/Chromium build: the real
                # login page is fetched instead (verified against live
                # linkedin.com, and confirmed with route interception logging
                # showing zero follow-up request to the fake Location and real
                # LinkedIn asset URLs appearing as sub-resource requests). The
                # same 302-fulfill technique works fine for other paths (see
                # test_goto_raises_when_logged_out / test_goto_checkpoint_after_navigation).
                # As a reliable offline alternative, simulate the redirect with an
                # inline script that runs during the initial parse, which Playwright
                # correctly tracks as the navigation's final destination.
                await route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body><script>location.replace('https://www.linkedin.com/feed/')</script></body></html>",
                )
            else:
                await route.fulfill(status=200, content_type="text/html", body="<html>feed</html>")

        await b._ctx.route("**/*", handler)

    b._start = patched_start
    try:
        result = await b.login(timeout_s=1)
        assert result.get("ok") is True
        assert "уже" in result.get("message", "").lower()
    finally:
        await b.close()
