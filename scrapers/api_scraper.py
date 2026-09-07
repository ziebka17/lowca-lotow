"""
Scrapery oparte na API porównywarek lotniczych.

Obsługiwane źródła (każde włącza się automatycznie, gdy w .env jest klucz):
  * Kiwi Tequila API  (KIWI_API_KEY)   – wyszukiwanie "z Polski dokądkolwiek"
  * SerpAPI Google Flights (SERPAPI_KEY) – wybrane trasy, darmowe 100 zapytań/mies.
  * Duffel API (DUFFEL_ACCESS_TOKEN)   – tryb testowy; sprawdza kilka tras

Każde źródło zwraca listę Deal z ceną w PLN. Wszystkie zapytania są opakowane
w try/except – awaria jednego API nie zatrzymuje pozostałych.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

import requests

from config import settings
from scrapers.base import BaseScraper, Deal
from utils import airports
from utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT = 25  # sekund na jedno zapytanie HTTP


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "error-fare-hunter/1.0 (+https://github.com)"})
    return s


# ====================================================================== #
# 1. Kiwi Tequila
# ====================================================================== #
class KiwiScraper(BaseScraper):
    """
    Kiwi Tequila – endpoint /v2/search z fly_to="anywhere" pozwala jednym
    zapytaniem zobaczyć najtańsze loty z danego lotniska w oknie dat.
    Uwaga: Kiwi ogranicza wydawanie nowych kluczy; jeśli nie masz klucza,
    zostaw KIWI_API_KEY puste – scraper się nie uruchomi.
    """

    name = "kiwi"
    BASE = "https://api.tequila.kiwi.com/v2/search"

    def enabled(self) -> bool:
        return bool(settings.kiwi_api_key)

    def fetch(self) -> List[Deal]:
        deals: List[Deal] = []
        session = _session()
        session.headers["apikey"] = settings.kiwi_api_key
        today = date.today()
        params_common = {
            "date_from": (today + timedelta(days=1)).strftime("%d/%m/%Y"),
            "date_to": (today + timedelta(days=settings.api_days_ahead)).strftime("%d/%m/%Y"),
            "nights_in_dst_from": 2,
            "nights_in_dst_to": 14,
            "flight_type": "round",
            "curr": "PLN",
            "sort": "price",
            "limit": 100,
            "one_for_city": 1,       # jedna (najtańsza) oferta na miasto
            "max_stopovers": 2,
        }
        for origin in settings.home_airports:
            try:
                params = dict(params_common, fly_from=origin, fly_to="anywhere")
                resp = session.get(self.BASE, params=params, timeout=TIMEOUT)
                resp.raise_for_status()
                for item in resp.json().get("data", []):
                    deals.append(self._to_deal(origin, item))
            except requests.RequestException as exc:
                log.warning("Kiwi: błąd zapytania dla %s: %s", origin, exc)
            except (ValueError, KeyError) as exc:
                log.warning("Kiwi: nieoczekiwany format odpowiedzi dla %s: %s", origin, exc)
        log.info("Kiwi: pobrano %d ofert", len(deals))
        return deals

    def _to_deal(self, origin: str, item: dict) -> Deal:
        dest = item.get("cityCodeTo") or item.get("flyTo", "???")
        route = item.get("route") or []
        outbound = route[0]["local_departure"][:10] if route else None
        inbound = next(
            (leg["local_departure"][:10] for leg in route if leg.get("return") == 1), None
        )
        return Deal(
            origin=origin,
            destination=dest,
            price_pln=float(item.get("price", 0)),
            source=self.name,
            url=item.get("deep_link", ""),
            date_from=outbound,
            date_to=inbound,
            description=f"{item.get('cityFrom')} → {item.get('cityTo')}, {item.get('countryTo', {}).get('name', '')}",
            country=(item.get("countryTo") or {}).get("code"),
            airline=",".join(item.get("airlines", [])[:2]),
        )


# ====================================================================== #
# 2. SerpAPI – Google Flights
# ====================================================================== #
class SerpApiScraper(BaseScraper):
    """
    Google Flights przez SerpAPI. Darmowy plan = 100 zapytań / miesiąc,
    dlatego sprawdzamy tylko wybrane trasy (API_DESTINATIONS) z pierwszego
    lotniska na liście HOME_AIRPORTS i tylko jedną datę w tygodniu.
    Oszczędnie: 1 zapytanie = 1 trasa.
    """

    name = "serpapi"
    BASE = "https://serpapi.com/search.json"

    def enabled(self) -> bool:
        return bool(settings.serpapi_key)

    def fetch(self) -> List[Deal]:
        deals: List[Deal] = []
        session = _session()
        origin = settings.home_airports[0] if settings.home_airports else "WAW"
        # Wyjazd za ~3 tygodnie, powrót po tygodniu – typowe „okienko” okazji.
        out = date.today() + timedelta(days=21)
        back = out + timedelta(days=7)
        for dest in settings.api_destinations[:8]:  # limit, by nie spalić kwoty
            try:
                params = {
                    "engine": "google_flights",
                    "departure_id": origin,
                    "arrival_id": dest,
                    "outbound_date": out.isoformat(),
                    "return_date": back.isoformat(),
                    "currency": "PLN",
                    "hl": "pl",
                    "api_key": settings.serpapi_key,
                }
                resp = session.get(self.BASE, params=params, timeout=TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                flights = (data.get("best_flights") or []) + (data.get("other_flights") or [])
                if not flights:
                    continue
                cheapest = min(flights, key=lambda f: f.get("price", 1e9))
                legs = cheapest.get("flights") or []
                deals.append(
                    Deal(
                        origin=origin,
                        destination=dest,
                        price_pln=float(cheapest.get("price", 0)),
                        source=self.name,
                        url=data.get("search_metadata", {}).get("google_flights_url", ""),
                        date_from=out.isoformat(),
                        date_to=back.isoformat(),
                        description=f"Google Flights: {origin} → {dest}, {len(legs)} odcinek/odcinki",
                        country=airports.IATA_COUNTRY.get(dest),
                        airline=legs[0].get("airline") if legs else None,
                    )
                )
            except requests.RequestException as exc:
                log.warning("SerpAPI: błąd zapytania %s→%s: %s", origin, dest, exc)
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("SerpAPI: zły format odpowiedzi %s→%s: %s", origin, dest, exc)
        log.info("SerpAPI: pobrano %d ofert", len(deals))
        return deals


# ====================================================================== #
# 3. Duffel (tryb testowy)
# ====================================================================== #
class DuffelScraper(BaseScraper):
    """
    Duffel – nowoczesne API NDC. W trybie testowym zwraca oferty testowe
    (Duffel Airways), więc służy głównie do sprawdzenia, że pipeline działa.
    W trybie live wymaga umowy – zostawiamy jako opcję.
    """

    name = "duffel"
    BASE = "https://api.duffel.com/air/offer_requests"

    def enabled(self) -> bool:
        return bool(settings.duffel_token)

    def fetch(self) -> List[Deal]:
        deals: List[Deal] = []
        session = _session()
        session.headers.update(
            {
                "Authorization": f"Bearer {settings.duffel_token}",
                "Duffel-Version": "v2",
                "Content-Type": "application/json",
            }
        )
        origin = settings.home_airports[0] if settings.home_airports else "WAW"
        out = date.today() + timedelta(days=30)
        back = out + timedelta(days=7)
        for dest in settings.api_destinations[:5]:
            try:
                body = {
                    "data": {
                        "slices": [
                            {"origin": origin, "destination": dest, "departure_date": out.isoformat()},
                            {"origin": dest, "destination": origin, "departure_date": back.isoformat()},
                        ],
                        "passengers": [{"type": "adult"}],
                        "cabin_class": "economy",
                    }
                }
                resp = session.post(
                    self.BASE, json=body, params={"return_offers": "true"}, timeout=TIMEOUT
                )
                resp.raise_for_status()
                offers = resp.json().get("data", {}).get("offers", [])
                if not offers:
                    continue
                cheapest = min(offers, key=lambda o: float(o.get("total_amount", 1e9)))
                amount = float(cheapest["total_amount"])
                currency = cheapest.get("total_currency", "PLN")
                rate = {"PLN": 1, "EUR": settings.eur_pln, "USD": settings.usd_pln, "GBP": settings.gbp_pln}.get(currency, 1)
                deals.append(
                    Deal(
                        origin=origin,
                        destination=dest,
                        price_pln=amount * rate,
                        source=self.name,
                        url="https://app.duffel.com/",
                        date_from=out.isoformat(),
                        date_to=back.isoformat(),
                        description=f"Duffel: {origin} → {dest} ({cheapest.get('owner', {}).get('name', '')})",
                        country=airports.IATA_COUNTRY.get(dest),
                        airline=cheapest.get("owner", {}).get("iata_code"),
                    )
                )
            except requests.RequestException as exc:
                log.warning("Duffel: błąd zapytania %s→%s: %s", origin, dest, exc)
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("Duffel: zły format odpowiedzi %s→%s: %s", origin, dest, exc)
        log.info("Duffel: pobrano %d ofert", len(deals))
        return deals


def all_api_scrapers() -> List[BaseScraper]:
    """Lista wszystkich scraperów API (włączone filtruje main.py)."""
    return [KiwiScraper(), SerpApiScraper(), DuffelScraper()]
