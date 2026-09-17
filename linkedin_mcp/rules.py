import re
from datetime import datetime

from . import limits, store
from .errors import error

COMMENT_GAP_S = (90, 150)
MIN_LEN, MAX_LEN = 20, 600
LINK_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def comment_precheck(conn, urn: str, text: str, comment_limit: int, confirm_not_posted: bool = False) -> dict | None:
    post = store.get_post(conn, urn)
    if post is None:
        return error("unknown_post", "Поста нет в базе. Сначала найди его через search_posts.")
    if post["is_vacancy"] != 1:
        return error("not_vacancy", "Комментировать можно только посты, сохранённые через save_vacancy.")
    if store.has_published_comment(conn, urn):
        return error("already_commented", "Под этим постом уже есть опубликованный комментарий.")
    if store.has_uncertain_attempt(conn, urn) and not confirm_not_posted:
        return error("uncertain_previous_attempt",
                     "Прошлая попытка могла опубликовать комментарий. Проверь пост вручную; повтори с "
                     "confirm_not_posted=True только если комментария под постом нет.")
    t = text.strip()
    if not MIN_LEN <= len(t) <= MAX_LEN:
        return error("bad_length", f"Длина комментария должна быть {MIN_LEN}–{MAX_LEN} символов, сейчас {len(t)}.")
    if LINK_RE.search(t):
        return error("has_link", "Ссылки в комментариях запрещены.")
    if not limits.check(conn, "comment", comment_limit):
        return error("limit_reached", f"Дневной лимит комментариев ({comment_limit}) исчерпан.")
    return None


def seconds_to_wait(last: datetime | None, now: datetime, gap: float) -> float:
    if last is None:
        return 0.0
    return max(0.0, gap - (now - last).total_seconds())
