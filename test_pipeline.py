import datetime as dt
from pathlib import Path

import pytest

from tcgtrack import analysis, collector, config, db, report
from tcgtrack.analysis import Point
from tcgtrack.source import TcgCsv, archive_ids

from . import fake_tcgcsv as fake


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("tcgcsv") / "site"
    fake.build_site(root)
    with fake.Server(root) as server:
        yield server.url


@pytest.fixture(scope="module")
def run(site, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("run")
    cfg_file = tmp / "config.toml"
    cfg_file.write_text((Path(__file__).parents[1] / "config.toml").read_text())
    cfg = config.load(cfg_file)
    conn = db.connect(cfg.db_path)
    log: list[str] = []
    latest = collector.update(cfg, conn, TcgCsv(site, pause=0), log=log.append)
    return cfg, conn, latest, log, site


def test_update_collects_live_and_archive(run):
    cfg, conn, latest, log, _ = run
    assert latest == fake.LATEST
    dates = db.snapshot_dates(conn, fake.CAT)
    assert fake.LATEST.isoformat() in dates
    assert (fake.LATEST - dt.timedelta(days=1)).isoformat() in dates
    assert (fake.LATEST - dt.timedelta(days=91)).isoformat() in dates
    assert fake.MISSING_ARCHIVE.isoformat() not in dates
    assert any("unavailable" in line for line in log)
    # the $1 bulk card clears min_store_price, so it is stored (but left out of the report)
    assert conn.execute("SELECT COUNT(*) FROM prices WHERE product_id = 7").fetchone()[0] > 0
    # the untracked category in the archive was ignored
    assert conn.execute("SELECT COUNT(*) FROM prices WHERE product_id NOT IN (SELECT product_id FROM products)"
                        ).fetchone()[0] == 0
    kinds = dict(conn.execute("SELECT product_id, kind FROM products WHERE product_id IN (1, 6)").fetchall())
    assert kinds == {1: "single", 6: "sealed"}


def test_update_is_idempotent(run):
    cfg, conn, _, _, site = run
    before = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    log: list[str] = []
    collector.update(cfg, conn, TcgCsv(site, pause=0), log=log.append)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == before
    assert any("already stored" in line for line in log)
    assert not any("backfilling" in line and str(fake.MISSING_ARCHIVE) not in line for line in log)


def test_signals(run):
    cfg, conn, *_ = run
    a = analysis.analyse(conn, cfg)
    by_id = {i.product_id: i for i in a.items}
    assert by_id[1].signal == analysis.BREAKOUT
    assert by_id[2].signal == analysis.CLIMBER
    assert by_id[3].signal == analysis.PEAKING
    assert by_id[4].signal == analysis.FALLING
    assert by_id[5].signal == analysis.REBOUND
    assert 7 not in by_id  # below the report's min_price
    assert by_id[1].ch7 == pytest.approx(1.03 ** 7 - 1, abs=0.01)
    assert by_id[2].r2 > 0.95
    # watchlist picks up the cheap Charizard even though it is under min_price
    assert [w.product_id for w in a.watched] == [8]


def test_report_files(run):
    cfg, conn, *_ = run
    a = analysis.analyse(conn, cfg)
    page, summary = report.render(a, cfg)
    html = page.read_text()
    assert page.name == f"report-{fake.LATEST.isoformat()}.html"
    assert "Breakout Dragon" in html and "Hot Expansion" in html and "Charizard ex" in html
    assert (cfg.output_dir / "latest.html").exists()
    assert "report-2026-10-25.html" in (cfg.output_dir / "index.html").read_text()
    assert "EARLY BREAKOUT" in summary and "Breakout Dragon" in summary
    sets = analysis.set_trends(a)
    assert sets[0]["set"] == "Hot Expansion" and sets[-1]["set"] == "Cold Expansion"


def test_resolve_games_prefers_exact_match():
    cats = [{"categoryId": 3, "name": "Pokemon", "displayName": "Pokémon"},
            {"categoryId": 85, "name": "Pokemon Japan", "displayName": "Pokémon Japan"}]
    assert collector.resolve_games(cats, ["pokemon"])[0]["categoryId"] == 3
    assert collector.resolve_games(cats, ["Pokemon Japan"])[0]["categoryId"] == 85
    with pytest.raises(SystemExit):
        collector.resolve_games(cats, ["Digimon"])


def test_wanted_dates_daily_then_weekly():
    latest = dt.date(2026, 10, 25)
    dates = collector.wanted_dates(latest, 14, 91)
    gaps = {(latest - d).days for d in dates}
    assert set(range(1, 15)) <= gaps
    assert {21, 28, 35, 91} <= gaps and 20 not in gaps


def test_value_near_prefers_closest_then_earlier():
    s = [Point(dt.date(2026, 1, d), float(d), None) for d in (1, 3, 5)]
    assert analysis.value_near(s, dt.date(2026, 1, 4), 2) == 3.0
    assert analysis.value_near(s, dt.date(2026, 1, 9), 2) is None


def test_archive_ids():
    assert archive_ids("2024-05-01/3/23237/prices") == (3, 23237)
    assert archive_ids("2024-05-01/3/23237") is None
