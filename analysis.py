"""Turns stored price history into momentum metrics and buy/sell signals."""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config, Signals

BREAKOUT, CLIMBER, PEAKING, REBOUND, FALLING = "breakout", "climber", "peaking", "rebound", "falling"

SIGNAL_INFO = {
    BREAKOUT: ("Early breakout", "Sharp, accelerating 7-day rise that hasn't run far yet. The earliest buy signal."),
    CLIMBER: ("Steady climber", "Clean, consistent uptrend over 30 days. Lower risk, slower move."),
    PEAKING: ("Possibly peaking", "Big 30-day run that is stalling or slowing down. Consider selling into strength."),
    REBOUND: ("Rebound", "Well off its 90-day high and turning back up."),
    FALLING: ("Falling", "Losing value. Avoid buying, or exit if you hold it."),
}


@dataclass
class Point:
    date: dt.date
    market: float | None
    low: float | None


@dataclass
class Item:
    product_id: int
    sub_type: str
    name: str
    set_name: str
    group_id: int
    category_id: int
    kind: str
    rarity: str | None
    number: str | None
    url: str | None
    image_url: str | None
    price: float
    low: float | None
    ch7: float | None = None
    ch_prev7: float | None = None
    ch30: float | None = None
    ch90: float | None = None
    accel: float | None = None
    low_ch7: float | None = None
    off_high: float | None = None
    r2: float | None = None
    up_ratio: float | None = None
    points: int = 0
    spark: list[tuple[dt.date, float]] = field(default_factory=list)
    signal: str | None = None
    score: float = 0.0
    tags: list[str] = field(default_factory=list)
    watched: bool = False

    @property
    def label(self) -> str:
        variant = "" if self.sub_type in ("Normal", "") else f" ({self.sub_type})"
        return f"{self.name}{variant}"


def value_near(series: list[Point], target: dt.date, tolerance: int, attr: str = "market") -> float | None:
    """Price on the stored date closest to `target` (preferring earlier dates on ties)."""
    best: tuple[int, int, float] | None = None
    for p in series:
        value = getattr(p, attr)
        if value is None:
            continue
        gap = abs((p.date - target).days)
        if gap <= tolerance:
            key = (gap, 0 if p.date <= target else 1, value)
            if best is None or key[:2] < best[:2]:
                best = key
    return best[2] if best else None


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return new / old - 1


def log_trend(series: list[Point], start: dt.date) -> tuple[float | None, float | None]:
    """Slope (per day) and R² of a straight-line fit to log price since `start`."""
    pts = [((p.date - start).days, math.log(p.market)) for p in series if p.date >= start and p.market]
    if len(pts) < 5:
        return None, None
    xs, ys = zip(*pts)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0:
        return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    r2 = 1.0 if syy == 0 else (sxy * sxy) / (sxx * syy)
    return slope, r2


def measure(item: Item, series: list[Point], asof: dt.date) -> None:
    now = item.price
    p7 = value_near(series, asof - dt.timedelta(days=7), 2)
    p14 = value_near(series, asof - dt.timedelta(days=14), 3)
    p30 = value_near(series, asof - dt.timedelta(days=30), 5)
    p90 = value_near(series, asof - dt.timedelta(days=90), 7)
    item.ch7, item.ch_prev7 = pct(now, p7), pct(p7, p14)
    item.ch30, item.ch90 = pct(now, p30), pct(now, p90)
    if item.ch7 is not None and item.ch_prev7 is not None:
        item.accel = item.ch7 - item.ch_prev7
    item.low_ch7 = pct(item.low, value_near(series, asof - dt.timedelta(days=7), 2, "low"))

    window = [p for p in series if p.market and p.date >= asof - dt.timedelta(days=90)]
    item.points = len(window)
    high = max((p.market for p in window), default=None)
    item.off_high = pct(now, high)
    slope, item.r2 = log_trend(series, asof - dt.timedelta(days=30))
    if slope is not None and slope < 0 and item.r2 is not None:
        item.r2 = -item.r2  # negative = clean downtrend

    recent = [p.market for p in series if p.market and p.date >= asof - dt.timedelta(days=7)]
    steps = list(zip(recent, recent[1:]))
    item.up_ratio = sum(b > a for a, b in steps) / len(steps) if len(steps) >= 3 else None
    item.spark = [(p.date, p.market) for p in window]


def classify(item: Item, s: Signals) -> None:
    ch7, ch30 = item.ch7, item.ch30
    if item.low_ch7 is not None and ch7 is not None and item.low_ch7 >= max(0.10, ch7 + 0.05):
        item.tags.append("supply tightening")
    if item.low is not None and item.low >= item.price * 1.02:
        item.tags.append("listings above market")
    if item.points < 6:
        item.tags.append("thin history")
    if ch7 is None:
        return

    accelerating = item.accel is None or item.accel > 0
    confirmed = item.up_ratio is None or item.up_ratio >= 0.5
    if item.off_high is not None and item.off_high <= s.rebound_off_high and ch7 >= s.rebound_min_7d:
        item.signal, item.score = REBOUND, ch7  # a bounce after a crash, not a fresh breakout
    elif ch7 >= s.breakout_min_7d and accelerating and confirmed and (ch30 is None or ch30 <= s.breakout_max_30d):
        item.signal = BREAKOUT
        supply = 0.25 if "supply tightening" in item.tags else 0
        item.score = ch7 + 0.5 * max(item.accel or 0, 0) + supply
    elif ch30 is not None and ch30 >= s.peak_min_30d and (ch7 <= 0.02 or (item.accel or 0) <= -0.10):
        item.signal, item.score = PEAKING, ch30
    elif ch30 is not None and ch30 >= s.climber_min_30d and (item.r2 or 0) >= s.climber_min_r2 and ch7 > 0:
        item.signal, item.score = CLIMBER, ch30 * item.r2
    elif ch7 <= s.falling_7d or (ch30 is not None and ch30 <= s.falling_30d):
        item.signal, item.score = FALLING, -min(ch7, ch30 if ch30 is not None else 0)


def watch_ids(conn: sqlite3.Connection, cfg: Config, category_ids: list[int]) -> set[int]:
    ids: set[int] = set()
    marks = ",".join("?" * len(category_ids))
    for w in cfg.watchlist:
        if "product_id" in w:
            ids.add(int(w["product_id"]))
            continue
        sql = (f"SELECT p.product_id FROM products p JOIN groups g USING (group_id) "
               f"WHERE p.category_id IN ({marks}) AND p.name LIKE ?")
        args: list = [*category_ids, f"%{w.get('name', '')}%"]
        if w.get("set"):
            sql += " AND g.name LIKE ?"
            args.append(f"%{w['set']}%")
        ids.update(r[0] for r in conn.execute(sql, args))
    return ids


@dataclass
class Analysis:
    asof: dt.date
    games: list[dict]
    items: list[Item]
    watched: list[Item]
    history_days: int
    daily_days: int


def latest_date(conn: sqlite3.Connection, category_ids: list[int]) -> dt.date | None:
    marks = ",".join("?" * len(category_ids))
    row = conn.execute(f"SELECT MAX(date) FROM snapshots WHERE category_id IN ({marks}) AND rows > 0",
                       category_ids).fetchone()
    return dt.date.fromisoformat(row[0]) if row and row[0] else None


def analyse(conn: sqlite3.Connection, cfg: Config, asof: dt.date | None = None) -> Analysis:
    games = [dict(r) for r in conn.execute("SELECT * FROM categories WHERE category_id IN "
                                           "(SELECT DISTINCT category_id FROM snapshots)")]
    tracked = {g.lower() for g in cfg.games}
    games = [g for g in games if g["name"].lower() in tracked or (g["display_name"] or "").lower() in tracked] or games
    cids = [g["category_id"] for g in games]
    if not cids:
        raise SystemExit("No price data yet. Run `python -m tcgtrack update` first.")
    asof = asof or latest_date(conn, cids)
    if asof is None:
        raise SystemExit("No price data yet. Run `python -m tcgtrack update` first.")
    start = asof - dt.timedelta(days=97)
    marks = ",".join("?" * len(cids))
    watched_ids = watch_ids(conn, cfg, cids)

    conn.execute("CREATE TEMP TABLE IF NOT EXISTS want (product_id INTEGER, sub_type TEXT)")
    conn.execute("DELETE FROM want")
    conn.execute(
        f"""INSERT INTO want SELECT pr.product_id, pr.sub_type FROM prices pr
            JOIN products p USING (product_id)
            WHERE pr.date = ? AND p.category_id IN ({marks}) AND pr.market >= ?""",
        [asof.isoformat(), *cids, cfg.min_price],
    )
    conn.executemany("INSERT INTO want SELECT product_id, sub_type FROM prices WHERE product_id = ? AND date = ?",
                     [(pid, asof.isoformat()) for pid in watched_ids])

    series: dict[tuple[int, str], list[Point]] = defaultdict(list)
    for r in conn.execute(
        """SELECT pr.product_id, pr.sub_type, pr.date, pr.market, pr.low FROM prices pr
           JOIN (SELECT DISTINCT product_id, sub_type FROM want) w USING (product_id, sub_type)
           WHERE pr.date BETWEEN ? AND ? ORDER BY pr.product_id, pr.sub_type, pr.date""",
        (start.isoformat(), asof.isoformat()),
    ):
        series[(r[0], r[1])].append(Point(dt.date.fromisoformat(r[2]), r[3], r[4]))

    meta = {r["product_id"]: r for r in conn.execute(
        f"""SELECT p.*, g.name AS set_name FROM products p JOIN groups g USING (group_id)
            WHERE p.category_id IN ({marks})""", cids)}

    items, watched = [], []
    for (pid, sub), pts in series.items():
        m = meta.get(pid)
        last = pts[-1]
        if m is None or last.date != asof or not last.market:
            continue
        item = Item(pid, sub, m["name"], m["set_name"], m["group_id"], m["category_id"], m["kind"],
                    m["rarity"], m["number"], m["url"], m["image_url"], last.market, last.low)
        measure(item, pts, asof)
        classify(item, cfg.signals)
        item.watched = pid in watched_ids
        if item.watched:
            watched.append(item)
        if item.price >= cfg.min_price:
            items.append(item)

    dates = sorted({p.date for pts in series.values() for p in pts})
    history_days = (asof - dates[0]).days if dates else 0
    recent = [d for d in dates if d >= asof - dt.timedelta(days=14)]
    watched.sort(key=lambda i: (i.name, i.set_name))
    return Analysis(asof, games, items, watched, history_days, len(recent))


# ---------------------------------------------------------------- summaries

def top(items: list[Item], signal: str, n: int) -> list[Item]:
    return sorted((i for i in items if i.signal == signal), key=lambda i: i.score, reverse=True)[:n]


def game_overview(a: Analysis) -> list[dict]:
    out = []
    for g in a.games:
        its = [i for i in a.items if i.category_id == g["category_id"] and i.ch7 is not None]
        ch7 = [i.ch7 for i in its]
        ch30 = [i.ch30 for i in its if i.ch30 is not None]
        counts = defaultdict(int)
        for i in its:
            counts[i.signal] += 1
        out.append({
            "name": g["display_name"] or g["name"],
            "tracked": len(its),
            "up": sum(c > 0.01 for c in ch7) / len(ch7) if ch7 else None,
            "down": sum(c < -0.01 for c in ch7) / len(ch7) if ch7 else None,
            "median7": statistics.median(ch7) if ch7 else None,
            "median30": statistics.median(ch30) if ch30 else None,
            "signals": dict(counts),
        })
    return out


def set_trends(a: Analysis, min_cards: int = 8) -> list[dict]:
    by_set: dict[int, list[Item]] = defaultdict(list)
    for i in a.items:
        if i.kind == "single" and i.ch7 is not None:
            by_set[i.group_id].append(i)
    rows = []
    for group_id, its in by_set.items():
        if len(its) < min_cards:
            continue
        ch30 = [i.ch30 for i in its if i.ch30 is not None]
        best = max(its, key=lambda i: i.ch7)
        rows.append({
            "set": its[0].set_name,
            "cards": len(its),
            "median7": statistics.median(i.ch7 for i in its),
            "median30": statistics.median(ch30) if ch30 else None,
            "up": sum(i.ch7 > 0.01 for i in its) / len(its),
            "value": sum(i.price for i in its),
            "leader": best,
        })
    return sorted(rows, key=lambda r: r["median7"], reverse=True)


def sealed(a: Analysis, n: int) -> list[Item]:
    its = [i for i in a.items if i.kind == "sealed" and i.ch7 is not None]
    return sorted(its, key=lambda i: (i.ch30 if i.ch30 is not None else i.ch7), reverse=True)[:n]
