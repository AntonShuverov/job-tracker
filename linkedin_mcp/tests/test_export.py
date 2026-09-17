from datetime import datetime

from linkedin_mcp import export, store


def _seed(conn):
    posts = [{"urn": f"urn:li:activity:{i}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{i}/",
              "author": "A", "text": "t"} for i in (1, 2, 3)]
    store.insert_posts(conn, posts, "q")
    store.save_vacancy(conn, "urn:li:activity:1", "Product | Owner", company="T-Bank", location="Москва")
    store.save_vacancy(conn, "urn:li:activity:2", "PM", company="2ГИС")
    store.add_comment(conn, "urn:li:activity:2", "Здравствуйте!\nИнтересна позиция " + "очень " * 30, "published")
    store.mark_commented(conn, "urn:li:activity:2")
    store.save_vacancy(conn, "urn:li:activity:3", "Head of Product")
    store.set_status(conn, "urn:li:activity:3", "skipped")


def test_render_sections(conn):
    _seed(conn)
    md = export.render(store.list_vacancies(conn, limit=1000), datetime(2026, 9, 17, 16, 40))
    assert md.startswith("# LinkedIn вакансии")
    assert "автоматически (2026-09-17 16:40)" in md
    assert "## Новые (1)" in md and "## Прокомментировано (1)" in md
    assert "## Откликнулся (0)" in md and "_пусто_" in md
    assert "## Отклонено / пропущено (1)" in md
    assert "Product \\| Owner" in md
    assert "[открыть](https://www.linkedin.com/feed/update/urn:li:activity:1/)" in md
    commented_row = next(l for l in md.splitlines() if "2ГИС" in l)
    assert "Здравствуйте! Интересна позиция" in commented_row and "…" in commented_row
    assert "\n" not in commented_row


def test_write_export_atomic(conn, tmp_path):
    _seed(conn)
    target = tmp_path / "vault" / "Jobs" / "LinkedIn вакансии.md"
    export.write_export(conn, target)
    assert target.read_text(encoding="utf-8").startswith("# LinkedIn вакансии")
    assert [p.name for p in target.parent.iterdir()] == ["LinkedIn вакансии.md"]
