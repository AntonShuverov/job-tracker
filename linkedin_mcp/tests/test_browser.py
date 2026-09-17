import json
import stat

from linkedin_mcp.browser import LinkedInBrowser, is_logged_out_url, is_profile_lock_error


def test_is_logged_out_url():
    assert is_logged_out_url("https://www.linkedin.com/authwall?trk=x")
    assert is_logged_out_url("https://www.linkedin.com/login")
    assert is_logged_out_url("https://www.linkedin.com/checkpoint/challenge/123")
    assert is_logged_out_url("https://www.linkedin.com/uas/login?session_redirect=x")
    assert not is_logged_out_url("https://www.linkedin.com/feed/")
    assert not is_logged_out_url("https://www.linkedin.com/search/results/content/?keywords=login")


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
