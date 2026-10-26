"""Loads config.toml into a typed Config object."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Signals:
    breakout_min_7d: float = 0.10
    breakout_max_30d: float = 0.60
    climber_min_30d: float = 0.12
    climber_min_r2: float = 0.70
    peak_min_30d: float = 0.40
    rebound_off_high: float = -0.30
    rebound_min_7d: float = 0.08
    falling_7d: float = -0.10
    falling_30d: float = -0.20


@dataclass
class Config:
    games: list[str] = field(default_factory=lambda: ["Pokemon"])
    db_path: Path = Path("data/prices.sqlite")
    min_store_price: float = 0.25
    daily_backfill_days: int = 14
    weekly_backfill_days: int = 91
    product_refresh_days: int = 7
    output_dir: Path = Path("reports")
    min_price: float = 5.0
    top_n: int = 25
    signals: Signals = field(default_factory=Signals)
    watchlist: list[dict] = field(default_factory=list)


def load(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    base = path.resolve().parent
    games = raw.get("games", {})
    storage = raw.get("storage", {})
    report = raw.get("report", {})

    cfg = Config()
    cfg.games = list(games.get("track", cfg.games))
    cfg.db_path = base / storage.get("db_path", str(cfg.db_path))
    cfg.min_store_price = float(storage.get("min_store_price", cfg.min_store_price))
    cfg.daily_backfill_days = int(storage.get("daily_backfill_days", cfg.daily_backfill_days))
    cfg.weekly_backfill_days = int(storage.get("weekly_backfill_days", cfg.weekly_backfill_days))
    cfg.product_refresh_days = int(storage.get("product_refresh_days", cfg.product_refresh_days))
    cfg.output_dir = base / report.get("output_dir", str(cfg.output_dir))
    cfg.min_price = float(report.get("min_price", cfg.min_price))
    cfg.top_n = int(report.get("top_n", cfg.top_n))
    cfg.signals = Signals(**{k: float(v) for k, v in raw.get("signals", {}).items()})
    cfg.watchlist = list(raw.get("watchlist", []))
    return cfg
