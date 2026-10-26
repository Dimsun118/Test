"""Keeps the local database current: catalog sync, today's prices, and archive backfill."""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Callable

from . import db
from .config import Config
from .source import ARCHIVE_START, SourceError, TcgCsv

Log = Callable[[str], None]


def resolve_games(categories: list[dict], wanted: list[str]) -> list[dict]:
    """Match configured game names to TCGplayer categories (exact name first, then substring)."""
    found = []
    for name in wanted:
        key = name.strip().lower()
        exact = [c for c in categories if key in ((c.get("name") or "").lower(), (c.get("displayName") or "").lower())]
        partial = [c for c in categories
                   if key in (c.get("name") or "").lower() or key in (c.get("displayName") or "").lower()]
        matches = exact or partial
        if len(matches) != 1:
            options = ", ".join(sorted(c["name"] for c in matches)) or "none"
            raise SystemExit(f"Game {name!r} matched {len(matches)} categories ({options}). "
                             "Run `python -m tcgtrack games` and use an exact name.")
        found.append(matches[0])
    return found


def wanted_dates(latest: dt.date, daily_days: int, weekly_days: int) -> list[dt.date]:
    """Daily points for the recent past, weekly points further back."""
    dates = {latest - dt.timedelta(days=d) for d in range(1, daily_days + 1)}
    dates |= {latest - dt.timedelta(days=d) for d in range(daily_days, weekly_days + 1, 7)}
    return sorted(d for d in dates if d >= ARCHIVE_START)


def sync_catalog(conn: sqlite3.Connection, src: TcgCsv, category_id: int, refresh_days: int,
                 today: dt.date, log: Log) -> list[int]:
    groups = src.groups(category_id)
    db.upsert_groups(conn, category_id, groups)
    stale_before = (today - dt.timedelta(days=refresh_days)).isoformat()
    stale = conn.execute(
        "SELECT group_id FROM groups WHERE category_id = ? AND (products_synced IS NULL OR products_synced < ?)",
        (category_id, stale_before),
    ).fetchall()
    if stale:
        log(f"  refreshing product list for {len(stale)} sets")
    for (group_id,) in stale:
        try:
            db.upsert_products(conn, src.products(category_id, group_id))
        except SourceError as exc:
            log(f"  ! skipped products for set {group_id}: {exc}")
            continue
        conn.execute("UPDATE groups SET products_synced = ? WHERE group_id = ?", (today.isoformat(), group_id))
        conn.commit()
    return [g["groupId"] for g in groups]


def collect_live(conn: sqlite3.Connection, src: TcgCsv, category_id: int, group_ids: list[int],
                 date: dt.date, min_price: float, log: Log) -> int:
    total = 0
    for group_id in group_ids:
        try:
            total += db.insert_prices(conn, date.isoformat(), src.prices(category_id, group_id), min_price)
        except SourceError as exc:
            log(f"  ! skipped prices for set {group_id}: {exc}")
    db.mark_snapshot(conn, date.isoformat(), category_id, "live", total)
    return total


def backfill(conn: sqlite3.Connection, src: TcgCsv, category_ids: list[int], dates: list[dt.date],
             min_price: float, log: Log) -> None:
    have = {cid: db.snapshot_dates(conn, cid) for cid in category_ids}
    for date in dates:
        missing = {cid for cid in category_ids if date.isoformat() not in have[cid]}
        if not missing:
            continue
        log(f"  backfilling {date} from archive")
        counts = dict.fromkeys(missing, 0)
        try:
            for cid, _group_id, rows in src.archive_prices(date, missing):
                counts[cid] += db.insert_prices(conn, date.isoformat(), rows, min_price)
        except SourceError as exc:
            log(f"  ! archive for {date} unavailable: {exc}")
            continue
        for cid, n in counts.items():
            db.mark_snapshot(conn, date.isoformat(), cid, "archive", n)


def update(cfg: Config, conn: sqlite3.Connection, src: TcgCsv | None = None, *, history: bool = True,
           log: Log = print) -> dt.date:
    """Bring the database up to date. Safe to run as often as you like."""
    src = src or TcgCsv()
    categories = src.categories()
    db.upsert_categories(conn, categories)
    games = resolve_games(categories, cfg.games)
    latest = src.last_updated()
    today = dt.datetime.now(dt.timezone.utc).date()

    for game in games:
        cid = game["categoryId"]
        log(f"{game.get('displayName') or game['name']}:")
        group_ids = sync_catalog(conn, src, cid, cfg.product_refresh_days, today, log)
        if latest.isoformat() in db.snapshot_dates(conn, cid):
            log(f"  prices for {latest} already stored")
        else:
            n = collect_live(conn, src, cid, group_ids, latest, cfg.min_store_price, log)
            log(f"  stored {n:,} prices for {latest}")

    if history:
        dates = wanted_dates(latest, cfg.daily_backfill_days, cfg.weekly_backfill_days)
        backfill(conn, src, [g["categoryId"] for g in games], dates, cfg.min_store_price, log)
    return latest
