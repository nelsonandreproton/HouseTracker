import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    district TEXT NOT NULL,
    municipality TEXT NOT NULL,
    parish TEXT,
    typology TEXT NOT NULL,
    kind TEXT NOT NULL,
    price REAL,
    price_label TEXT NOT NULL,
    sale_type TEXT NOT NULL,
    url TEXT NOT NULL,
    end_date TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, external_id)
);
CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    ok INTEGER NOT NULL,
    last_error TEXT,
    last_run TEXT NOT NULL,
    last_raw_total INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

UPSERT = """
INSERT INTO listings (source, external_id, title, district, municipality, parish, typology,
    kind, price, price_label, sale_type, url, end_date, first_seen, last_seen, active)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
ON CONFLICT(source, external_id) DO UPDATE SET
    title=excluded.title, district=excluded.district, municipality=excluded.municipality,
    parish=excluded.parish, typology=excluded.typology, kind=excluded.kind,
    price=excluded.price, price_label=excluded.price_label, sale_type=excluded.sale_type,
    url=excluded.url, end_date=excluded.end_date, last_seen=excluded.last_seen, active=1
"""


@dataclass(frozen=True)
class SourceStatus:
    source: str
    ok: bool
    last_error: str | None
    last_run: datetime
    last_raw_total: int | None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str) -> None:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_listings(conn: sqlite3.Connection, listings: list[Listing], now: datetime) -> list[Listing]:
    """Insert or refresh listings; return the ones never seen before."""
    new = []
    for item in listings:
        exists = conn.execute(
            "SELECT 1 FROM listings WHERE source = ? AND external_id = ?",
            (item.source, item.external_id),
        ).fetchone()
        if not exists:
            new.append(item)
        conn.execute(UPSERT, (
            item.source, item.external_id, item.title, item.district, item.municipality,
            item.parish, item.typology, item.kind, item.price, item.price_label,
            item.sale_type, item.url, _iso(item.end_date), _iso(now), _iso(now),
        ))
    conn.commit()
    return new


def deactivate_unseen(conn: sqlite3.Connection, source: str, now: datetime) -> int:
    """Deactivate the source's listings not refreshed in the run stamped `now`."""
    cur = conn.execute(
        "UPDATE listings SET active = 0 WHERE source = ? AND active = 1 AND last_seen < ?",
        (source, _iso(now)),
    )
    conn.commit()
    return cur.rowcount


def _row_to_listing(row: sqlite3.Row) -> Listing:
    return Listing(
        source=row["source"], external_id=row["external_id"], title=row["title"],
        district=row["district"], municipality=row["municipality"], parish=row["parish"],
        typology=row["typology"], kind=row["kind"], price=row["price"],
        price_label=row["price_label"], sale_type=row["sale_type"], url=row["url"],
        end_date=_dt(row["end_date"]), first_seen=_dt(row["first_seen"]),
        last_seen=_dt(row["last_seen"]), active=bool(row["active"]),
    )


def get_listing(conn: sqlite3.Connection, source: str, external_id: str) -> Listing | None:
    row = conn.execute(
        "SELECT * FROM listings WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()
    return _row_to_listing(row) if row else None


def list_active(
    conn: sqlite3.Connection,
    now: datetime,
    district: str | None = None,
    kind: str | None = None,
    sale_type: str | None = None,
) -> list[Listing]:
    """Active, not-yet-ended listings sorted by price ascending."""
    sql = "SELECT * FROM listings WHERE active = 1"
    params: list[str] = []
    for column, value in (("district", district), ("kind", kind), ("sale_type", sale_type)):
        if value:
            sql += f" AND {column} = ?"  # column names come from the fixed tuple above
            params.append(value)
    sql += " ORDER BY price ASC"
    items = [_row_to_listing(r) for r in conn.execute(sql, params)]
    return [i for i in items if i.end_date is None or i.end_date > now]


def count_active(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM listings WHERE active = 1").fetchone()[0]


def get_status(conn: sqlite3.Connection, source: str) -> SourceStatus | None:
    row = conn.execute("SELECT * FROM source_status WHERE source = ?", (source,)).fetchone()
    if not row:
        return None
    return SourceStatus(row["source"], bool(row["ok"]), row["last_error"],
                        datetime.fromisoformat(row["last_run"]), row["last_raw_total"])


def all_statuses(conn: sqlite3.Connection) -> list[SourceStatus]:
    names = [r["source"] for r in conn.execute("SELECT source FROM source_status ORDER BY source")]
    return [get_status(conn, n) for n in names]


def set_status(conn: sqlite3.Connection, source: str, ok: bool, error: str | None,
               raw_total: int | None, now: datetime) -> None:
    conn.execute(
        "INSERT INTO source_status (source, ok, last_error, last_run, last_raw_total) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET ok=excluded.ok, "
        "last_error=excluded.last_error, last_run=excluded.last_run, "
        "last_raw_total=excluded.last_raw_total",
        (source, int(ok), error, _iso(now), raw_total),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
