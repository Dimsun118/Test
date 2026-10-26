"""SQLite storage for the catalog and daily price snapshots."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    category_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    display_name TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    group_id        INTEGER PRIMARY KEY,
    category_id     INTEGER NOT NULL,
    name            TEXT NOT NULL,
    abbreviation    TEXT,
    published_on    TEXT,
    products_synced TEXT
);
CREATE TABLE IF NOT EXISTS products (
    product_id  INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL,
    group_id    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    url         TEXT,
    image_url   TEXT,
    rarity      TEXT,
    number      TEXT,
    kind        TEXT NOT NULL  -- 'single' or 'sealed'
);
CREATE INDEX IF NOT EXISTS products_group ON products (group_id);
CREATE TABLE IF NOT EXISTS prices (
    date       TEXT    NOT NULL,
    product_id INTEGER NOT NULL,
    sub_type   TEXT    NOT NULL,  -- Normal, Holofoil, Reverse Holofoil, Foil, ...
    market     REAL,
    low        REAL,
    mid        REAL,
    high       REAL,
    direct_low REAL,
    PRIMARY KEY (product_id, sub_type, date)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS prices_date ON prices (date);
CREATE TABLE IF NOT EXISTS snapshots (
    date        TEXT    NOT NULL,
    category_id INTEGER NOT NULL,
    source      TEXT    NOT NULL,  -- 'live' or 'archive'
    rows        INTEGER NOT NULL,
    PRIMARY KEY (date, category_id)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_categories(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO categories VALUES (?, ?, ?)",
        [(r["categoryId"], r["name"], r.get("displayName")) for r in rows],
    )
    conn.commit()


def upsert_groups(conn: sqlite3.Connection, category_id: int, rows: list[dict]) -> None:
    conn.executemany(
        """INSERT INTO groups (group_id, category_id, name, abbreviation, published_on) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (group_id) DO UPDATE SET name = excluded.name, abbreviation = excluded.abbreviation,
                                                published_on = excluded.published_on""",
        [(r["groupId"], category_id, r["name"], r.get("abbreviation"), r.get("publishedOn")) for r in rows],
    )
    conn.commit()


def upsert_products(conn: sqlite3.Connection, rows: list[dict]) -> None:
    def row(p: dict) -> tuple:
        ext = {e.get("name"): e.get("value") for e in p.get("extendedData") or []}
        rarity, number = ext.get("Rarity"), ext.get("Number")
        kind = "single" if (rarity or number) else "sealed"
        return (p["productId"], p["categoryId"], p["groupId"], p["name"], p.get("url"),
                p.get("imageUrl"), rarity, number, kind)

    conn.executemany("INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [row(p) for p in rows])


def insert_prices(conn: sqlite3.Connection, date: str, rows: list[dict], min_price: float) -> int:
    keep = []
    for r in rows:
        ref = r.get("marketPrice") or r.get("lowPrice") or r.get("midPrice")
        if ref is None or ref < min_price:
            continue
        keep.append((date, r["productId"], r.get("subTypeName") or "Normal", r.get("marketPrice"),
                     r.get("lowPrice"), r.get("midPrice"), r.get("highPrice"), r.get("directLowPrice")))
    conn.executemany("INSERT OR REPLACE INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", keep)
    return len(keep)


def mark_snapshot(conn: sqlite3.Connection, date: str, category_id: int, source: str, rows: int) -> None:
    conn.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)", (date, category_id, source, rows))
    conn.commit()


def snapshot_dates(conn: sqlite3.Connection, category_id: int) -> set[str]:
    return {r[0] for r in conn.execute("SELECT date FROM snapshots WHERE category_id = ?", (category_id,))}
