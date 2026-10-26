"""Client for TCGCSV (https://tcgcsv.com), a free daily mirror of TCGplayer's catalog and prices.

Live endpoints refresh once a day (around 20:00 UTC). Daily price archives go back to
2024-02-08, which lets a fresh install backfill months of history immediately.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Iterator

import requests

BASE_URL = "https://tcgcsv.com"
ARCHIVE_START = dt.date(2024, 2, 8)
USER_AGENT = "tcg-tracker/1.0 (personal price tracker; weekly reports)"


class SourceError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class TcgCsv:
    def __init__(self, base_url: str = BASE_URL, pause: float = 0.05, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.pause = pause
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _get(self, path: str, stream: bool = False) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(4):
            try:
                resp = self.session.get(url, timeout=120, stream=stream)
            except requests.RequestException as exc:
                if attempt == 3:
                    raise SourceError(f"GET {url} failed: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 404:
                raise SourceError(f"GET {url}: not found", status=404)
            if resp.status_code >= 500 or resp.status_code == 429:
                if attempt == 3:
                    raise SourceError(f"GET {url}: HTTP {resp.status_code}", status=resp.status_code)
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise SourceError(f"GET {url}: HTTP {resp.status_code}", status=resp.status_code)
            time.sleep(self.pause)
            return resp
        raise AssertionError("unreachable")

    def _results(self, path: str) -> list[dict]:
        data = self._get(path).json()
        if not data.get("success", True):
            raise SourceError(f"{path}: {data.get('errors')}")
        return data.get("results", [])

    def categories(self) -> list[dict]:
        return self._results("tcgplayer/categories")

    def groups(self, category_id: int) -> list[dict]:
        return self._results(f"tcgplayer/{category_id}/groups")

    def products(self, category_id: int, group_id: int) -> list[dict]:
        return self._results(f"tcgplayer/{category_id}/{group_id}/products")

    def prices(self, category_id: int, group_id: int) -> list[dict]:
        return self._results(f"tcgplayer/{category_id}/{group_id}/prices")

    def last_updated(self) -> dt.date:
        """UTC date of the most recent live price refresh."""
        text = self._get("last-updated.txt").text.strip()
        try:
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.timezone.utc).date()
        except ValueError:
            match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            if not match:
                raise SourceError(f"Unrecognised last-updated value: {text!r}")
            return dt.date.fromisoformat(match.group())

    def archive_prices(self, date: dt.date, category_ids: set[int]) -> Iterator[tuple[int, int, list[dict]]]:
        """Yield (category_id, group_id, price rows) from the archived snapshot for `date`."""
        import py7zr

        resp = self._get(f"archive/tcgplayer/prices-{date.isoformat()}.ppmd.7z", stream=True)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "prices.7z"
            with archive.open("wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
            out = Path(tmp) / "x"
            with py7zr.SevenZipFile(archive) as zf:
                names = zf.getnames()
                wanted = [n for n in names if (ids := archive_ids(n)) and ids[0] in category_ids]
                targets = set(wanted)
                for name in wanted:  # py7zr wants parent directories listed too
                    parts = name.split("/")
                    targets.update("/".join(parts[:i]) for i in range(1, len(parts)))
                if wanted:
                    zf.extract(path=out, targets=sorted(targets & set(names)))
            for file in sorted(out.rglob("prices")) if out.exists() else []:
                ids = archive_ids(file.relative_to(out).as_posix())
                if ids and ids[0] in category_ids and file.is_file():
                    yield ids[0], ids[1], json.loads(file.read_text()).get("results", [])


def archive_ids(name: str) -> tuple[int, int] | None:
    """`2024-05-01/3/23237/prices` -> (3, 23237)."""
    parts = name.strip("/").split("/")
    if len(parts) >= 3 and parts[-1] == "prices" and parts[-2].isdigit() and parts[-3].isdigit():
        return int(parts[-3]), int(parts[-2])
    return None
