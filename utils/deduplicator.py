"""
Deduplikacja i historia cen – SQLite (jeden plik data/seen_deals.db).

Tabele:
  seen_deals    – odciski (fingerprint) ofert, o których już wysłaliśmy maila
  price_history – obserwacje cen z API, do liczenia mediany („cena standardowa”)

W GitHub Actions plik bazy jest po każdym uruchomieniu commitowany z powrotem
do repozytorium, dzięki czemu stan przetrwa między uruchomieniami.
"""
from __future__ import annotations

import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)


class Deduplicator:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        # Bez trybu WAL – baza ma być JEDNYM plikiem, który łatwo commitować w Actions.
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_deals (
                    fingerprint TEXT PRIMARY KEY,
                    route       TEXT,
                    price_pln   REAL,
                    source      TEXT,
                    url         TEXT,
                    seen_at     TEXT
                );
                CREATE TABLE IF NOT EXISTS price_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    route       TEXT NOT NULL,
                    price_pln   REAL NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_route ON price_history(route);
                CREATE TABLE IF NOT EXISTS state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Deduplikacja
    # ------------------------------------------------------------------ #
    def is_seen(self, fingerprint: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_deals WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return row is not None

    def mark_seen(self, fingerprint: str, route: str, price_pln: float, source: str, url: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_deals VALUES (?,?,?,?,?,?)",
                (fingerprint, route, price_pln, source, url, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def purge_old(self, days: int = 60) -> None:
        """Usuń stare wpisy, żeby baza nie rosła w nieskończoność."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(self._connect()) as conn:
            deleted = conn.execute("DELETE FROM seen_deals WHERE seen_at < ?", (cutoff,)).rowcount
            deleted += conn.execute("DELETE FROM price_history WHERE observed_at < ?", (cutoff,)).rowcount
            conn.commit()
        if deleted:
            log.debug("Usunięto %d starych wpisów z bazy", deleted)

    # ------------------------------------------------------------------ #
    # Historia cen (do wykrywania odchyleń)
    # ------------------------------------------------------------------ #
    def record_price(self, route: str, price_pln: float) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO price_history (route, price_pln, observed_at) VALUES (?,?,?)",
                (route, price_pln, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def baseline_price(self, route: str, min_samples: int) -> Optional[float]:
        """Mediana obserwowanych cen na trasie; None, gdy za mało danych."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT price_pln FROM price_history WHERE route = ? ORDER BY observed_at DESC LIMIT 200",
                (route,),
            ).fetchall()
        prices = [r[0] for r in rows]
        if len(prices) < min_samples:
            return None
        return statistics.median(prices)

    # ------------------------------------------------------------------ #
    # Drobny stan (np. ID ostatniej wiadomości z kanału Telegram)
    # ------------------------------------------------------------------ #
    def get_state(self, key: str) -> Optional[str]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, value))
            conn.commit()
