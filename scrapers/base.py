"""
Wspólny model danych (Deal) i klasa bazowa scrapera.

Każdy scraper zwraca listę obiektów Deal. Dzięki jednemu formatowi analizator,
deduplikator i notifier nie muszą wiedzieć, skąd oferta pochodzi.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class Deal:
    """Pojedyncza oferta lotu / wykryta okazja."""

    origin: str                       # kod IATA lotniska wylotu, np. "WAW"
    destination: str                  # kod IATA / miasta docelowego, np. "BCN"
    price_pln: float                  # cena przeliczona na PLN
    source: str                       # skąd: "kiwi", "telegram:fly4free", "rss:loter"...
    url: str                          # bezpośredni link do rezerwacji / posta
    date_from: Optional[str] = None   # "YYYY-MM-DD" lub tekst z posta
    date_to: Optional[str] = None
    description: str = ""             # krótki opis (np. fragment posta)
    country: Optional[str] = None     # kraj docelowy (ISO-2) do klasyfikacji
    airline: Optional[str] = None
    is_roundtrip: bool = True
    # Ustawiane przez analizator:
    reason: str = ""                  # dlaczego uznano za okazję
    baseline_pln: Optional[float] = None  # mediana historyczna, jeśli znana
    found_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def route(self) -> str:
        return f"{self.origin}-{self.destination}"

    def fingerprint(self) -> str:
        """
        Stabilny identyfikator oferty do deduplikacji.

        Dla ofert z API: trasa + daty + zaokrąglona cena.
        Dla postów (Telegram/RSS/FB): URL posta jest najlepszym kluczem –
        ten sam post nie powinien wywołać drugiego maila.
        """
        if self.source.startswith(("telegram", "rss", "facebook", "web")) and self.url:
            key = f"{self.source}|{self.url}"
        else:
            price_bucket = int(round(self.price_pln / 10.0))  # tolerancja ±5 PLN
            key = f"{self.origin}|{self.destination}|{self.date_from}|{self.date_to}|{price_bucket}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    def short(self) -> str:
        """Jednolinijkowy opis do logów."""
        dates = f" {self.date_from}→{self.date_to}" if self.date_from else ""
        return f"{self.route}{dates} {self.price_pln:.0f} PLN [{self.source}]"


class BaseScraper:
    """
    Interfejs scrapera. Podklasy implementują `fetch()` i zwracają listę Deal.
    `name` używane jest w logach i w polu Deal.source.
    """

    name: str = "base"

    def enabled(self) -> bool:
        """Czy scraper ma wymagane klucze/konfigurację (domyślnie tak)."""
        return True

    def fetch(self) -> List[Deal]:
        raise NotImplementedError


# --- Pomocnicze parsery tekstu (wspólne dla Telegrama, RSS, Facebooka) --------

_PRICE_PATTERNS = [
    # "199 zł", "199zł", "199 PLN", "1 299 zł", "1.299 zł"
    (re.compile(r"(\d{1,3}(?:[ . ]?\d{3})*|\d+)\s*(?:zł|zl|pln)\b", re.I), "PLN"),
    # "od 19 €", "19€", "19 EUR", "€19" – symbol waluty nie tworzy granicy słowa,
    # dlatego \b stoi tylko po nazwach literowych (eur/usd/gbp).
    (re.compile(r"(\d{1,3}(?:[ . ]?\d{3})*|\d+)\s*(?:€|eur\b)", re.I), "EUR"),
    (re.compile(r"€\s*(\d+)", re.I), "EUR"),
    (re.compile(r"\$\s*(\d+)|(\d+)\s*(?:usd\b|\$)", re.I), "USD"),
    (re.compile(r"£\s*(\d+)|(\d+)\s*(?:gbp\b|£)", re.I), "GBP"),
]


def extract_price_pln(text: str, eur_pln: float, usd_pln: float, gbp_pln: float) -> Optional[float]:
    """
    Wyciągnij NAJNIŻSZĄ cenę z tekstu i przelicz ją na PLN.
    Posty często zawierają kilka cen („od 39 zł”, „normalnie 400 zł”) –
    interesuje nas ta najniższa (oferta), a nie referencyjna.
    """
    rates = {"PLN": 1.0, "EUR": eur_pln, "USD": usd_pln, "GBP": gbp_pln}
    found: List[float] = []
    for pattern, currency in _PRICE_PATTERNS:
        for match in pattern.finditer(text or ""):
            raw = next((g for g in match.groups() if g), None)
            if not raw:
                continue
            digits = re.sub(r"[^\d]", "", raw)
            if not digits:
                continue
            value = float(digits) * rates[currency]
            if 5 <= value <= 20000:  # odfiltruj rok „2026”, numery lotów itp.
                found.append(value)
    return min(found) if found else None


_DATE_RANGE = re.compile(
    r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\s*[-–—]\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?"
)
_MONTHS_PL = (
    "styczeń|stycznia|styczniu|luty|lutego|lutym|marzec|marca|marcu|kwiecień|kwietnia|kwietniu|"
    "maj|maja|maju|czerwiec|czerwca|czerwcu|lipiec|lipca|lipcu|sierpień|sierpnia|sierpniu|"
    "wrzesień|września|wrześniu|październik|października|październiku|"
    "listopad|listopada|listopadzie|grudzień|grudnia|grudniu"
)
_MONTH_MENTION = re.compile(rf"\b({_MONTHS_PL})\b(?:\s*\d{{4}})?", re.I)


def extract_dates(text: str) -> tuple:
    """
    Spróbuj wyciągnąć daty podróży z tekstu.
    Zwraca (date_from, date_to) jako tekst; jeśli nie ma konkretnych dat,
    zwraca wzmiankę o miesiącu (np. „w październiku”) lub (None, None).
    """
    m = _DATE_RANGE.search(text or "")
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        year1 = y1 or y2 or str(datetime.now().year)
        year2 = y2 or year1
        year1 = year1 if len(year1) == 4 else "20" + year1
        year2 = year2 if len(year2) == 4 else "20" + year2
        return f"{year1}-{int(m1):02d}-{int(d1):02d}", f"{year2}-{int(m2):02d}-{int(d2):02d}"
    months = _MONTH_MENTION.findall(text or "")
    if months:
        return months[0].lower(), (months[1].lower() if len(months) > 1 else None)
    return None, None


def extract_first_url(text: str) -> Optional[str]:
    """Pierwszy link http(s) w tekście (do przycisku „Rezerwuj”)."""
    m = re.search(r"https?://[^\s<>\"\)\]]+", text or "")
    return m.group(0).rstrip(".,") if m else None
