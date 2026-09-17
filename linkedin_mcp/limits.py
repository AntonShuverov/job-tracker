from datetime import date


def _day(day: str | None) -> str:
    return day or date.today().isoformat()


def used(conn, kind: str, day: str | None = None) -> int:
    row = conn.execute("SELECT count FROM actions WHERE date = ? AND kind = ?", (_day(day), kind)).fetchone()
    return row["count"] if row else 0


def check(conn, kind: str, limit: int, day: str | None = None) -> bool:
    return used(conn, kind, day) < limit


def consume(conn, kind: str, day: str | None = None) -> None:
    conn.execute(
        "INSERT INTO actions (date, kind, count) VALUES (?, ?, 1) "
        "ON CONFLICT(date, kind) DO UPDATE SET count = count + 1",
        (_day(day), kind),
    )


def refund(conn, kind: str, day: str | None = None) -> None:
    conn.execute("UPDATE actions SET count = MAX(count - 1, 0) WHERE date = ? AND kind = ?", (_day(day), kind))


def status(conn, limits_cfg: dict[str, int], day: str | None = None) -> dict[str, dict]:
    out = {}
    for kind, limit in limits_cfg.items():
        n = used(conn, kind, day)
        out[kind] = {"used": n, "limit": limit, "left": max(0, limit - n)}
    return out
