"""Renders the weekly market report (HTML + a short text summary)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from . import analysis as an
from .config import Config


def fmt_pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "–"
    return f"{x * 100:+.{digits}f}%"


def fmt_money(x: float | None) -> str:
    if x is None:
        return "–"
    return f"${x:,.2f}" if x < 1000 else f"${x:,.0f}"


def delta(x: float | None, digits: int = 1) -> Markup:
    """Percentage with a direction icon; the icon carries the colour, the number stays in text ink."""
    if x is None:
        return Markup('<span class="na">–</span>')
    if x > 0.005:
        icon, cls = "▲", "up"
    elif x < -0.005:
        icon, cls = "▼", "down"
    else:
        icon, cls = "■", "flat"
    return Markup(f'<span class="delta"><span class="ic {cls}" aria-hidden="true">{icon}</span>'
                  f'{fmt_pct(x, digits)}</span>')


def sparkline(points: list[tuple[dt.date, float]], width: int = 112, height: int = 30) -> Markup:
    if len(points) < 2:
        return Markup('<span class="na">not enough history</span>')
    d0 = points[0][0]
    xs = [(d - d0).days for d, _ in points]
    ys = [p for _, p in points]
    span_x = max(xs[-1], 1)
    lo, hi = min(ys), max(ys)
    span_y = (hi - lo) or 1
    pad = 3
    coords = [(pad + (x / span_x) * (width - 2 * pad), pad + (1 - (y - lo) / span_y) * (height - 2 * pad))
              for x, y in zip(xs, ys)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    ex, ey = coords[-1]
    title = (f"{points[0][0]:%b %d} {fmt_money(ys[0])} → {points[-1][0]:%b %d} {fmt_money(ys[-1])} "
             f"(range {fmt_money(lo)}–{fmt_money(hi)})")
    return Markup(
        f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        f'<polyline points="{path}" fill="none" stroke="var(--series-1)" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3" fill="var(--series-1)" stroke="var(--surface-1)" '
        f'stroke-width="1.5"/></svg>'
    )


def explorer_rows(items: list[an.Item], limit: int = 800) -> list[dict]:
    ranked = sorted(items, key=lambda i: abs(i.ch7 or 0), reverse=True)[:limit]
    return [{
        "n": i.label, "s": i.set_name, "r": i.rarity or ("Sealed" if i.kind == "sealed" else ""),
        "k": i.kind, "p": round(i.price, 2),
        "c7": None if i.ch7 is None else round(i.ch7, 4),
        "c30": None if i.ch30 is None else round(i.ch30, 4),
        "c90": None if i.ch90 is None else round(i.ch90, 4),
        "sig": i.signal or "", "u": i.url or "",
        "sp": [round(p, 2) for _, p in i.spark][-30:],
    } for i in ranked]


def build_context(a: an.Analysis, cfg: Config) -> dict:
    n = cfg.top_n
    return {
        "asof": a.asof,
        "generated": dt.datetime.now(dt.timezone.utc),
        "games": an.game_overview(a),
        "game_names": ", ".join(g["display_name"] or g["name"] for g in a.games),
        "history_days": a.history_days,
        "daily_days": a.daily_days,
        "min_price": cfg.min_price,
        "sections": [
            (an.BREAKOUT, an.top(a.items, an.BREAKOUT, n)),
            (an.CLIMBER, an.top(a.items, an.CLIMBER, n)),
            (an.PEAKING, an.top(a.items, an.PEAKING, n)),
            (an.REBOUND, an.top(a.items, an.REBOUND, n)),
            (an.FALLING, an.top(a.items, an.FALLING, n)),
        ],
        "signal_info": an.SIGNAL_INFO,
        "signal_names": json.dumps({k: v[0] for k, v in an.SIGNAL_INFO.items()}),
        "sets_up": [s for s in an.set_trends(a)[:12] if s["median7"] > 0],
        "sets_down": [s for s in reversed(an.set_trends(a)[-8:]) if s["median7"] < 0],
        "sealed": an.sealed(a, n),
        "watched": a.watched,
        "explorer": json.dumps(explorer_rows(a.items)),
        "tracked": len(a.items),
    }


def summary_text(ctx: dict) -> str:
    lines = [f"TCG market report — week ending {ctx['asof']:%a %d %b %Y} ({ctx['game_names']})", ""]
    for g in ctx["games"]:
        lines.append(f"{g['name']}: {g['tracked']:,} items ≥ ${ctx['min_price']:.0f}; "
                     f"{fmt_pct(g['up'], 0).lstrip('+')} rising, median 7d {fmt_pct(g['median7'])}")
    for key, items in ctx["sections"][:3]:
        if not items:
            continue
        lines += ["", f"{an.SIGNAL_INFO[key][0].upper()}"]
        for i in items[:8]:
            lines.append(f"  • {i.label} [{i.set_name}] {fmt_money(i.price)}  7d {fmt_pct(i.ch7)}  "
                         f"30d {fmt_pct(i.ch30)}" + (f"  ({', '.join(i.tags)})" if i.tags else ""))
    if ctx["sets_up"]:
        lines += ["", "SETS HEATING UP"]
        lines += [f"  • {s['set']}: median 7d {fmt_pct(s['median7'])}, {s['up'] * 100:.0f}% of cards rising"
                  for s in ctx["sets_up"][:5]]
    return "\n".join(lines) + "\n"


def render(a: an.Analysis, cfg: Config) -> tuple[Path, str]:
    env = Environment(loader=PackageLoader("tcgtrack", "templates"), autoescape=select_autoescape())
    env.filters.update(pct=fmt_pct, money=fmt_money, delta=delta, spark=sparkline)
    ctx = build_context(a, cfg)
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    page = out / f"report-{a.asof.isoformat()}.html"
    page.write_text(env.get_template("report.html.j2").render(**ctx))
    shutil.copyfile(page, out / "latest.html")
    summary = summary_text(ctx)
    (out / f"report-{a.asof.isoformat()}.txt").write_text(summary)

    reports = sorted(out.glob("report-*.html"), reverse=True)
    (out / "index.html").write_text(env.get_template("index.html.j2").render(
        reports=[(p.name, p.stem.removeprefix("report-")) for p in reports]))
    return page, summary
