import os
from datetime import datetime
from pathlib import Path

from . import store

SECTIONS = [
    ("Новые", ("new",)),
    ("Прокомментировано", ("commented",)),
    ("Откликнулся", ("applied",)),
    ("Отклонено / пропущено", ("rejected", "skipped")),
]
HEADER = "| Дата | Должность | Компания | Локация | Комментарий | Пост |"


def _cell(value, max_len: int | None = None) -> str:
    s = " ".join(str(value or "").split())
    if max_len and len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s.replace("|", "\\|")


def render(vacancies: list[dict], now: datetime) -> str:
    lines = [
        "# LinkedIn вакансии",
        "",
        f"> Файл генерируется автоматически ({now:%Y-%m-%d %H:%M}). Ручные правки будут перезаписаны.",
        "",
    ]
    for title, statuses in SECTIONS:
        rows = [v for v in vacancies if v["status"] in statuses]
        lines += [f"## {title} ({len(rows)})", ""]
        if not rows:
            lines += ["_пусто_", ""]
            continue
        lines += [HEADER, "|---|---|---|---|---|---|"]
        for v in rows:
            cells = [
                _cell((v["first_seen"] or "")[:10]),
                _cell(v["title"]),
                _cell(v["company"]),
                _cell(v["location"]),
                _cell(v["comment"], 80),
                f"[открыть]({v['url']})",
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def write_export(conn, path: Path, now: datetime | None = None) -> None:
    text = render(store.list_vacancies(conn, limit=100000), now or datetime.now())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
