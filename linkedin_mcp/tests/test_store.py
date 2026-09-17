import sqlite3
from datetime import datetime

import pytest

from linkedin_mcp import store


def _post(n: int, text: str = "Ищем продакт-менеджера в команду платежей, удалёнка, опыт от 3 лет") -> dict:
    return {"urn": f"urn:li:activity:{n}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{n}/",
            "author": f"Автор {n}", "text": text}


def test_insert_posts_dedup(conn):
    new, seen = store.insert_posts(conn, [_post(1), _post(2)], "q1")
    assert [p["urn"] for p in new] == ["urn:li:activity:1", "urn:li:activity:2"] and seen == 0
    new, seen = store.insert_posts(conn, [_post(2), _post(3)], "q2")
    assert [p["urn"] for p in new] == ["urn:li:activity:3"] and seen == 1
    assert store.get_post(conn, "urn:li:activity:2")["query"] == "q1"


def test_unreviewed_and_classification(conn):
    store.insert_posts(conn, [_post(1), _post(2), _post(3)], "q")
    assert store.save_vacancy(conn, "urn:li:activity:1", "Product Manager", company="T-Bank")
    assert store.mark_not_vacancy(conn, ["urn:li:activity:2", "urn:li:activity:404"]) == 1
    assert [p["urn"] for p in store.list_unreviewed(conn)] == ["urn:li:activity:3"]
    assert not store.save_vacancy(conn, "urn:li:activity:404", "PM")
    vac = store.list_vacancies(conn)
    assert len(vac) == 1 and vac[0]["status"] == "new" and vac[0]["company"] == "T-Bank"


def test_save_vacancy_keeps_existing_status(conn):
    store.insert_posts(conn, [_post(1)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM")
    store.set_status(conn, "urn:li:activity:1", "applied")
    store.save_vacancy(conn, "urn:li:activity:1", "Senior PM")
    v = store.list_vacancies(conn)[0]
    assert v["status"] == "applied" and v["title"] == "Senior PM"


def test_set_status_validation(conn):
    store.insert_posts(conn, [_post(1)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM")
    with pytest.raises(ValueError):
        store.set_status(conn, "urn:li:activity:1", "hired")
    assert store.set_status(conn, "urn:li:activity:1", "rejected")
    assert store.list_vacancies(conn, status="rejected")[0]["urn"] == "urn:li:activity:1"
    assert store.list_vacancies(conn, status="new") == []


def test_comments_log(conn):
    store.insert_posts(conn, [_post(1), _post(2)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM", company="2ГИС")
    assert store.last_published_at(conn) is None
    store.add_comment(conn, "urn:li:activity:1", "черновик", "failed", error="submit_not_found")
    assert not store.has_published_comment(conn, "urn:li:activity:1")
    store.add_comment(conn, "urn:li:activity:1", "Здравствуйте! Интересна позиция", "published")
    store.mark_commented(conn, "urn:li:activity:1")
    assert store.has_published_comment(conn, "urn:li:activity:1")
    assert isinstance(store.last_published_at(conn), datetime)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_comment(conn, "urn:li:activity:1", "второй", "published")
    log = store.list_comments(conn)
    assert [c["status"] for c in log] == ["published", "failed"]
    assert log[0]["company"] == "2ГИС" and log[0]["url"].endswith("activity:1/")
    v = store.list_vacancies(conn)[0]
    assert v["status"] == "commented" and v["comment"] == "Здравствуйте! Интересна позиция"


def test_last_attempt_at_counts_failed_rows(conn):
    store.insert_posts(conn, [_post(1)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM")
    assert store.last_attempt_at(conn) is None
    store.add_comment(conn, "urn:li:activity:1", "текст", "failed", error="submit_not_found")
    assert isinstance(store.last_attempt_at(conn), datetime)
    assert store.last_published_at(conn) is None


def test_has_uncertain_attempt(conn):
    store.insert_posts(conn, [_post(1)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM")
    assert not store.has_uncertain_attempt(conn, "urn:li:activity:1")
    store.add_comment(conn, "urn:li:activity:1", "текст", "failed", error="submit_not_found")
    assert not store.has_uncertain_attempt(conn, "urn:li:activity:1")
    store.add_comment(conn, "urn:li:activity:1", "текст", "failed", error="not_confirmed")
    assert store.has_uncertain_attempt(conn, "urn:li:activity:1")


def test_mark_commented_does_not_override_applied(conn):
    store.insert_posts(conn, [_post(1)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM")
    store.set_status(conn, "urn:li:activity:1", "applied")
    store.mark_commented(conn, "urn:li:activity:1")
    assert store.list_vacancies(conn)[0]["status"] == "applied"


def test_attempt_lifecycle(conn):
    store.insert_posts(conn, [_post(1), _post(2)], "q")
    store.save_vacancy(conn, "urn:li:activity:1", "PM")
    cid = store.start_attempt(conn, "urn:li:activity:1", "текст")
    assert store.has_uncertain_attempt(conn, "urn:li:activity:1")
    store.finish_attempt(conn, cid, "failed", error="submit_not_found")
    assert not store.has_uncertain_attempt(conn, "urn:li:activity:1")
    cid2 = store.start_attempt(conn, "urn:li:activity:1", "текст")
    store.finish_attempt(conn, cid2, "published")
    assert store.has_published_comment(conn, "urn:li:activity:1")
    assert [(c["status"], c["error"]) for c in store.list_comments(conn)] == [("published", None),
                                                                             ("failed", "submit_not_found")]


def test_has_published_comment_for_text(conn):
    store.insert_posts(conn, [_post(1), _post(2)], "q")
    text = store.get_post(conn, "urn:li:activity:1")["text"]
    assert not store.has_published_comment_for_text(conn, text)
    store.add_comment(conn, "urn:li:activity:1", "комментарий", "published")
    assert store.has_published_comment_for_text(conn, text)
    assert not store.has_published_comment_for_text(conn, "другой текст")
