# TCG Tracker

Tracks trading card game prices every day and builds a **weekly market report** that shows,
at a glance, which cards and sets are starting to move, so you can get in before a trend peaks
and get out when it stalls.

- **Any game on TCGplayer**: Pokémon, Magic, Yu-Gi-Oh!, One Piece, Lorcana, Pokémon Japan, and more.
- **Free data, no API key**: TCGplayer market prices via [TCGCSV](https://tcgcsv.com), refreshed daily.
- **History from day one**: the first run backfills ~3 months from TCGCSV's daily archives.
- **Runs itself**: a GitHub Actions workflow collects prices daily and publishes the report every Sunday,
  with optional email.

## What the report shows

| Section | What it means | What to do |
|---|---|---|
| **Early breakout** | Up ≥10% this week, *accelerating* (faster than last week), not already up >60% this month, and rising on most days | The earliest buy signal. Check recent sales and get in. |
| **Steady climber** | Up ≥12% over 30 days on a clean, consistent trend line | Lower risk and slower. Good holds. |
| **Possibly peaking** | Up ≥40% this month but flat or slowing this week | Consider selling into strength. |
| **Rebound** | ≥30% below its 90-day high and up ≥8% this week | A possible recovery, or a dead-cat bounce. |
| **Falling** | Down ≥10% this week or ≥20% this month | Avoid, or exit. |
| **Sets heating up / cooling off** | Whole sets moving together (median of their singles) | Often an out-of-print or meta shift. Look at the whole set. |
| **Sealed product** | Booster boxes, ETBs, and similar, ranked by 30-day move | |
| **Watchlist** | Cards you always want to see | |
| **Explore all movers** | Searchable, sortable table of the biggest movers | |

Tags on an item:

- **supply tightening**: the cheapest listing rose clearly faster than the market price, meaning cheap copies
  are being bought up. This often comes before the market price catches up.
- **listings above market**: the cheapest listing is now above the market price, so the market price is
  likely to follow it up.
- **thin history**: few data points, so treat the item with caution.

Every threshold is configurable in `config.toml` under `[signals]`.

## Quick start (on your computer)

```bash
# needs Python 3.11 or newer
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m tcgtrack games      # list game names you can track
# edit config.toml: pick games, minimum price, watchlist
python -m tcgtrack update     # first run downloads ~25 days of archives (a few minutes)
python -m tcgtrack report     # writes reports/latest.html; open it in your browser
```

After that, run `python -m tcgtrack weekly` once a week (update and report together). It is also safe
to run `update` daily. Daily runs give the report finer-grained data, and a missed day is backfilled
from the archive automatically.

## Run it automatically on GitHub (recommended)

1. Push this folder to a new GitHub repository.
2. **Settings → Pages → Source: GitHub Actions** (Pages needs a public repo on a free plan).
3. **Actions → TCG prices → Run workflow** once, to build the history and your first report.

From then on it runs every evening (~21:41 UTC) and publishes a new report every Sunday at
`https://<you>.github.io/<repo>/latest.html`. Past reports are listed at `index.html` and committed under `reports/`.

**Email** (optional): add these repository secrets (Settings → Secrets and variables → Actions):
`SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_TO`.
For Gmail, use `smtp.gmail.com` and an [App Password](https://myaccount.google.com/apppasswords).
Set the repository *variable* `REPORT_URL` to your Pages URL to put a link in the email.

## Configuration

See `config.toml`. The settings you'll most likely change:

- `games.track`: e.g. `["Pokemon", "One Piece Card Game"]`. Use names exactly as `tcgtrack games` prints them.
- `report.min_price`: items below this are left out of the report (default $5, to filter out bulk noise).
- `[[watchlist]]`: by `product_id = 123456` (the number in a TCGplayer product URL), or by `name` plus an optional `set`.

## Good to know

- **No sales volume.** TCGplayer's market price follows recent sales, but this feed has no volume data,
  so a card that rarely sells can jump on a single sale. The "rising on most days" check and the
  minimum price reduce that noise. Always check the card's recent sales on TCGplayer before buying.
- Prices are TCGplayer USD market prices. Each printing (Holofoil, Reverse Holofoil, 1st Edition, …) is tracked separately.
- Not financial advice.

## Development

```bash
pip install pytest
python -m pytest
```

The tests run the full pipeline against a local fake of the TCGCSV API (real HTTP and real 7z archives),
using synthetic cards with known price shapes.

Layout: `tcgtrack/source.py` (TCGCSV client), `collector.py` (sync and backfill), `db.py` (SQLite),
`analysis.py` (metrics and signals), `report.py` plus `templates/` (HTML report), `emailer.py`.
