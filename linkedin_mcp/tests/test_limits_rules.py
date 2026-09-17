from datetime import datetime, timedelta, timezone

from linkedin_mcp import limits, rules, store

URN = "urn:li:activity:1"
GOOD = "Здравствуйте! Интересна позиция продакта, 5 лет в финтехе. Буду рад пообщаться."


def _vacancy(conn):
    store.insert_posts(conn, [{"urn": URN, "url": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
                               "author": "A", "text": "Ищем продакта"}], "q")
    store.save_vacancy(conn, URN, "PM")


def test_limits_count_per_day(conn):
    assert limits.check(conn, "comment", 2, day="2026-09-17")
    limits.consume(conn, "comment", day="2026-09-17")
    limits.consume(conn, "comment", day="2026-09-17")
    assert limits.used(conn, "comment", day="2026-09-17") == 2
    assert not limits.check(conn, "comment", 2, day="2026-09-17")
    assert limits.check(conn, "comment", 2, day="2026-09-18")
    st = limits.status(conn, {"search": 15, "comment": 2}, day="2026-09-17")
    assert st == {"search": {"used": 0, "limit": 15, "left": 15},
                  "comment": {"used": 2, "limit": 2, "left": 0}}


def test_precheck_ok(conn):
    _vacancy(conn)
    assert rules.comment_precheck(conn, URN, GOOD, 8) is None


def test_precheck_errors(conn):
    assert rules.comment_precheck(conn, URN, GOOD, 8)["error"] == "unknown_post"
    store.insert_posts(conn, [{"urn": URN, "url": "u", "author": "A", "text": "t"}], "q")
    assert rules.comment_precheck(conn, URN, GOOD, 8)["error"] == "not_vacancy"
    store.save_vacancy(conn, URN, "PM")
    assert rules.comment_precheck(conn, URN, "коротко", 8)["error"] == "bad_length"
    assert rules.comment_precheck(conn, URN, "x" * 601, 8)["error"] == "bad_length"
    assert rules.comment_precheck(conn, URN, GOOD + " https://t.me/me", 8)["error"] == "has_link"
    assert rules.comment_precheck(conn, URN, GOOD + " www.site.ru", 8)["error"] == "has_link"
    assert rules.comment_precheck(conn, URN, GOOD, 0)["error"] == "limit_reached"
    store.add_comment(conn, URN, GOOD, "published")
    assert rules.comment_precheck(conn, URN, GOOD, 8)["error"] == "already_commented"


def test_precheck_uncertain_previous_attempt(conn):
    _vacancy(conn)
    store.add_comment(conn, URN, GOOD, "failed", error="submit_not_found")
    assert rules.comment_precheck(conn, URN, GOOD, 8) is None

    store.add_comment(conn, URN, GOOD, "failed", error="not_confirmed")
    assert rules.comment_precheck(conn, URN, GOOD, 8)["error"] == "uncertain_previous_attempt"
    assert rules.comment_precheck(conn, URN, GOOD, 8, confirm_not_posted=True) is None


def test_seconds_to_wait():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    assert rules.seconds_to_wait(None, now, 120) == 0
    assert rules.seconds_to_wait(now - timedelta(seconds=30), now, 120) == 90
    assert rules.seconds_to_wait(now - timedelta(seconds=500), now, 120) == 0
