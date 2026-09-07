"""
Analizator cen – decyduje, czy Deal jest okazją wartą powiadomienia.

Dwa niezależne kryteria (wystarczy jedno):
  1. Sztywne progi: Europa < MAX_PRICE_EUROPE_PLN, daleki dystans < MAX_PRICE_LONGHAUL_PLN.
  2. Odchylenie od normy: cena < PRICE_DROP_RATIO * mediana historyczna trasy
     (mediana budowana jest z wyników API zapisywanych przy każdym uruchomieniu).

Dodatkowo filtr geograficzny: wylot musi być z lotniska z listy HOME_AIRPORTS.
"""
from __future__ import annotations

from typing import List

from config import settings
from scrapers.base import Deal
from utils import airports
from utils.deduplicator import Deduplicator
from utils.logger import get_logger

log = get_logger(__name__)


class PriceAnalyzer:
    def __init__(self, dedup: Deduplicator):
        self.dedup = dedup

    # ------------------------------------------------------------------ #
    def passes_geo_filter(self, deal: Deal) -> bool:
        """Czy wylot jest z jednego z naszych lotnisk."""
        return deal.origin.upper() in {a.upper() for a in settings.home_airports}

    def _country(self, deal: Deal) -> str | None:
        return deal.country or airports.IATA_COUNTRY.get(deal.destination.upper())

    def hard_threshold_reason(self, deal: Deal) -> str | None:
        """Sprawdź sztywne progi cenowe."""
        longhaul = airports.is_longhaul(self._country(deal))
        if longhaul and deal.price_pln <= settings.max_price_longhaul:
            return f"daleki dystans ≤ {settings.max_price_longhaul:.0f} PLN"
        if not longhaul and deal.price_pln <= settings.max_price_europe:
            return f"Europa ≤ {settings.max_price_europe:.0f} PLN"
        return None

    def deviation_reason(self, deal: Deal) -> str | None:
        """Sprawdź odchylenie od mediany historycznej."""
        baseline = self.dedup.baseline_price(deal.route, settings.min_history_samples)
        if baseline is None:
            return None
        deal.baseline_pln = baseline
        if deal.price_pln <= baseline * settings.price_drop_ratio:
            drop = (1 - deal.price_pln / baseline) * 100
            return f"−{drop:.0f}% vs. mediana {baseline:.0f} PLN"
        return None

    # ------------------------------------------------------------------ #
    def analyze(self, deals: List[Deal]) -> List[Deal]:
        """
        Zwróć tylko oferty, które są okazją I nie były jeszcze wysłane.
        Każdej zaakceptowanej ofercie ustawia `reason`.
        """
        accepted: List[Deal] = []
        for deal in deals:
            try:
                if not self.passes_geo_filter(deal):
                    log.debug("Odrzucono (geo): %s", deal.short())
                    continue

                # Zapisz obserwację do historii – TYLKO dla źródeł API,
                # posty z Telegrama to już „przefiltrowane” okazje i zaniżałyby medianę.
                if deal.source in {"kiwi", "serpapi", "duffel"}:
                    self.dedup.record_price(deal.route, deal.price_pln)

                reason = self.hard_threshold_reason(deal) or self.deviation_reason(deal)

                # Posty z kanałów łowców okazji: jeśli nie znamy ceny lub jest
                # powyżej progów, wciąż mogą być ciekawe – ale nie spamujemy.
                if not reason:
                    log.debug("Odrzucono (cena): %s", deal.short())
                    continue

                if self.dedup.is_seen(deal.fingerprint()):
                    log.debug("Pominięto duplikat: %s", deal.short())
                    continue

                deal.reason = reason
                accepted.append(deal)
            except Exception as exc:  # noqa: BLE001 – jedna zła oferta nie ma zatrzymać reszty
                log.warning("Błąd analizy oferty %s: %s", deal, exc)
        return accepted
