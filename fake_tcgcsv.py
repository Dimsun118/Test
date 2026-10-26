"""A local stand-in for tcgcsv.com, serving synthetic prices with known shapes over real HTTP."""
from __future__ import annotations

import datetime as dt
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import py7zr

LATEST = dt.date(2026, 10, 25)
CAT = 3
SET_HOT, SET_COLD, SET_OBF = 100, 200, 300
MISSING_ARCHIVE = LATEST - dt.timedelta(days=5)


def flat(v):
    return lambda d: v


def breakout(d):  # flat, then accelerating over the last ~12 days
    return 20 * 1.03 ** max(0, 12 - d)


def climber(d):  # clean exponential uptrend
    return 10 * 1.006 ** (91 - d)


def peaking(d):  # doubled from day 40 to day 8, now slipping
    if d >= 40:
        return 10.0
    if d >= 8:
        return 10 * 2 ** ((40 - d) / 32)
    return 20 - 0.15 * (8 - d)


def falling(d):
    return 50 * 0.98 ** (91 - d)


def rebound(d):  # crashed from 40 to 20, bouncing
    if d >= 30:
        return 40.0
    if d >= 8:
        return 40 - 20 * (30 - d) / 22
    return 20 * 1.02 ** (8 - d)


def sealed_box(d):
    return 120 * 1.004 ** (91 - d)


# product id -> (group, name, rarity, price function, sub_type)
PRODUCTS = {
    1: (SET_HOT, "Breakout Dragon", "Special Illustration Rare", breakout, "Holofoil"),
    2: (SET_HOT, "Climbing Fox", "Ultra Rare", climber, "Holofoil"),
    3: (SET_COLD, "Peaking Owl", "Illustration Rare", peaking, "Holofoil"),
    4: (SET_COLD, "Falling Star", "Ultra Rare", falling, "Holofoil"),
    5: (SET_COLD, "Rebound Wolf", "Ultra Rare", rebound, "Holofoil"),
    6: (SET_HOT, "Hot Set Booster Box", None, sealed_box, None),
    7: (SET_HOT, "Bulk Common", "Common", flat(1.0), "Normal"),
    8: (SET_OBF, "Charizard ex - 125/197", "Double Rare", flat(3.0), "Holofoil"),
}
# filler singles so the two sets have enough cards for set-level trends
for k in range(10):
    PRODUCTS[100 + k] = (SET_HOT, f"Hot Filler {k}", "Rare", (lambda d, k=k: (8 + k) * 1.004 ** (91 - d)), "Holofoil")
    PRODUCTS[200 + k] = (SET_COLD, f"Cold Filler {k}", "Rare", (lambda d, k=k: (8 + k) * 0.996 ** (91 - d)), "Holofoil")

GROUPS = {SET_HOT: "Hot Expansion", SET_COLD: "Cold Expansion", SET_OBF: "SV03: Obsidian Flames"}


def price_rows(group: int, days_ago: int) -> list[dict]:
    rows = []
    for pid, (g, _name, _rarity, fn, sub) in PRODUCTS.items():
        if g != group:
            continue
        market = round(fn(days_ago), 2)
        rows.append({"productId": pid, "lowPrice": round(market * 0.9, 2), "midPrice": market,
                     "highPrice": round(market * 1.5, 2), "marketPrice": market, "directLowPrice": None,
                     "subTypeName": sub})
    return rows


def ok(results) -> str:
    return json.dumps({"success": True, "errors": [], "totalItems": len(results), "results": results})


def build_site(root: Path) -> None:
    def put(rel: str, text: str) -> None:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    put("last-updated.txt", f"{LATEST.isoformat()}T20:05:31+0000")
    put("tcgplayer/categories", ok([
        {"categoryId": 3, "name": "Pokemon", "displayName": "Pokémon"},
        {"categoryId": 85, "name": "Pokemon Japan", "displayName": "Pokémon Japan"},
        {"categoryId": 1, "name": "Magic", "displayName": "Magic: The Gathering"},
    ]))
    put(f"tcgplayer/{CAT}/groups", ok([{"groupId": g, "name": n, "abbreviation": n[:3].upper(),
                                        "publishedOn": "2025-01-01T00:00:00", "categoryId": CAT}
                                       for g, n in GROUPS.items()]))
    for g in GROUPS:
        products = []
        for pid, (pg, name, rarity, _fn, _sub) in PRODUCTS.items():
            if pg == g:
                ext = [{"name": "Rarity", "displayName": "Rarity", "value": rarity},
                       {"name": "Number", "displayName": "Card Number", "value": str(pid)}] if rarity else []
                products.append({"productId": pid, "name": name, "cleanName": name, "categoryId": CAT,
                                 "groupId": g, "url": f"https://www.tcgplayer.com/product/{pid}",
                                 "imageUrl": "", "extendedData": ext})
        put(f"tcgplayer/{CAT}/{g}/products", ok(products))
        put(f"tcgplayer/{CAT}/{g}/prices", ok(price_rows(g, 0)))

    staging = root.parent / "staging"
    for days_ago in range(1, 100):
        date = LATEST - dt.timedelta(days=days_ago)
        if date == MISSING_ARCHIVE:
            continue
        day_dir = staging / date.isoformat()
        for g in GROUPS:
            f = day_dir / str(CAT) / str(g) / "prices"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(ok(price_rows(g, days_ago)))
        other = day_dir / "1" / "999" / "prices"  # a category we don't track
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(ok([]))
        archive = root / "archive" / "tcgplayer" / f"prices-{date.isoformat()}.ppmd.7z"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with py7zr.SevenZipFile(archive, "w", filters=[{"id": py7zr.FILTER_PPMD}]) as zf:
            zf.writeall(day_dir, arcname=date.isoformat())


class Server:
    def __init__(self, root: Path):
        handler = functools.partial(_QuietHandler, directory=str(root))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
