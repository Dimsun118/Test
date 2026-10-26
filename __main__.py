"""Command line: python -m tcgtrack {games,update,report,weekly}."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from . import analysis, collector, config, db, emailer, report
from .source import TcgCsv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tcgtrack", description="Track TCG prices and build weekly market reports.")
    parser.add_argument("--config", default="config.toml", help="path to config.toml (default: ./config.toml)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("games", help="list every game (TCGplayer category) you can track")
    up = sub.add_parser("update", help="download today's prices and backfill missing history")
    up.add_argument("--no-history", action="store_true", help="skip the archive backfill")
    rep = sub.add_parser("report", help="build the market report from stored prices")
    rep.add_argument("--date", type=dt.date.fromisoformat, help="report as of this date (default: latest)")
    rep.add_argument("--email", action="store_true", help="email the report (needs SMTP_* env vars)")
    wk = sub.add_parser("weekly", help="update, then build (and email, if configured) the report")
    wk.add_argument("--no-history", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "games":
        for c in sorted(TcgCsv().categories(), key=lambda c: c["name"].lower()):
            print(f"{c['categoryId']:>5}  {c['name']}" + (f"  ({c['displayName']})" if c.get("displayName")
                                                         and c["displayName"] != c["name"] else ""))
        return 0

    cfg = config.load(args.config)
    conn = db.connect(cfg.db_path)
    if args.cmd in ("update", "weekly"):
        collector.update(cfg, conn, history=not args.no_history)
    if args.cmd in ("report", "weekly"):
        result = analysis.analyse(conn, cfg, getattr(args, "date", None))
        page, summary = report.render(result, cfg)
        print(summary)
        print(f"Report written to {page}")
        if getattr(args, "email", False) or (args.cmd == "weekly" and emailer.configured()):
            if not emailer.configured():
                print("Email skipped: set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and REPORT_TO.", file=sys.stderr)
            else:
                emailer.send(page, summary, os.environ.get("REPORT_URL"))
                print(f"Emailed to {os.environ['REPORT_TO']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
