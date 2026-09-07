"""
Scrapery stron WWW i Facebooka.

  * RssScraper       – kanały RSS portali z okazjami (Fly4free, Loter, Pepper…).
                       Najstabilniejsze źródło: RSS rzadko zmienia format.
  * AirlinePromoScraper – podstrony promocyjne Ryanair / Wizz Air / LOT
                       (BeautifulSoup + fallback Playwright, gdy strona
                       renderuje się w JavaScripcie).
  * FacebookScraper  – publiczne strony FB przez Playwright (opcjonalny,
                       ENABLE_FACEBOOK=1). Facebook agresywnie blokuje boty,
                       więc traktuj to źródło jako „best effort”.

Każdy wpis jest parsowany tymi samymi funkcjami co posty z Telegrama
(extract_price_pln, find_origin, find_destination).
"""
from __future__ import annotations

import re
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config import settings
from scrapers.base import (
    BaseScraper,
    Deal,
    extract_dates,
    extract_price_pln,
)
from utils import airports
from utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT = 25
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}


def text_to_deal(text: str, source: str, url: str, title: str = "") -> Optional[Deal]:
    """Wspólna konwersja tekst -> Deal (jak w Telegramie)."""
    full = f"{title}\n{text}"
    origin = airports.find_origin(full, settings.home_airports)
    if not origin:
        return None
    price = extract_price_pln(full, settings.eur_pln, settings.usd_pln, settings.gbp_pln)
    if price is None:
        return None
    dest = airports.find_destination(full)
    date_from, date_to = extract_dates(full)
    return Deal(
        origin=origin,
        destination=dest[0] if dest else "???",
        country=dest[1] if dest else None,
        price_pln=price,
        source=source,
        url=url,
        date_from=date_from,
        date_to=date_to,
        description=(title or text).strip()[:400],
    )


def _strip_html(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


# ====================================================================== #
# 1. RSS
# ====================================================================== #
class RssScraper(BaseScraper):
    name = "rss"

    def enabled(self) -> bool:
        return bool(settings.rss_feeds)

    def fetch(self) -> List[Deal]:
        deals: List[Deal] = []
        for feed_url in settings.rss_feeds:
            try:
                resp = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
                site = re.sub(r"^www\.", "", requests.utils.urlparse(feed_url).netloc)
                for entry in parsed.entries[:30]:
                    title = entry.get("title", "")
                    summary = _strip_html(entry.get("summary", "") or entry.get("description", ""))
                    link = entry.get("link", feed_url)
                    deal = text_to_deal(summary, f"rss:{site}", link, title=title)
                    if deal:
                        deals.append(deal)
            except requests.RequestException as exc:
                log.warning("RSS: błąd pobierania %s: %s", feed_url, exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("RSS: błąd parsowania %s: %s", feed_url, exc)
        log.info("RSS: znaleziono %d potencjalnych ofert", len(deals))
        return deals


# ====================================================================== #
# 2. Strony promocyjne linii lotniczych
# ====================================================================== #
AIRLINE_PAGES = {
    "ryanair": "https://www.ryanair.com/pl/pl/oferty",
    "wizzair": "https://wizzair.com/pl-pl/informacje-i-uslugi/promocje",
    "lot": "https://www.lot.com/pl/pl/promocje",
}


class AirlinePromoScraper(BaseScraper):
    """
    Pobiera stronę promocji linii i szuka fragmentów z ceną i miastem.
    Najpierw requests+BeautifulSoup; jeśli w HTML nie ma cen (SPA), próbuje
    Playwright (jeśli zainstalowany) – renderuje stronę w Chromium.
    """

    name = "web"

    def enabled(self) -> bool:
        return settings.enable_airline_pages

    def fetch(self) -> List[Deal]:
        deals: List[Deal] = []
        for airline, url in AIRLINE_PAGES.items():
            try:
                html = self._get_html(url)
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                # Dzielimy stronę na „kafelki” – elementy zawierające cenę.
                candidates = soup.find_all(string=re.compile(r"\d+\s*(zł|PLN|€)", re.I))
                seen_blocks = set()
                for node in candidates[:60]:
                    block = node.find_parent(["article", "li", "div", "a", "section"])
                    if block is None:
                        continue
                    text = block.get_text(" ", strip=True)[:500]
                    if text in seen_blocks:
                        continue
                    seen_blocks.add(text)
                    link_tag = block.find("a", href=True) if block.name != "a" else block
                    link = link_tag["href"] if link_tag else url
                    if link.startswith("/"):
                        link = requests.compat.urljoin(url, link)
                    deal = text_to_deal(text, f"web:{airline}", link)
                    if deal:
                        deal.airline = airline
                        deals.append(deal)
            except Exception as exc:  # noqa: BLE001
                log.warning("WWW: błąd dla %s: %s", airline, exc)
        log.info("WWW (linie): znaleziono %d potencjalnych ofert", len(deals))
        return deals

    def _get_html(self, url: str) -> Optional[str]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            if re.search(r"\d+\s*(zł|PLN)", resp.text):
                return resp.text
        except requests.RequestException as exc:
            log.debug("WWW: requests nie dał rady dla %s (%s) – próbuję Playwright", url, exc)
        return render_with_playwright(url)


# ====================================================================== #
# 3. Facebook (Playwright)
# ====================================================================== #
class FacebookScraper(BaseScraper):
    """
    Odczyt publicznych stron FB (np. https://www.facebook.com/fly4free).
    Wymaga: `playwright install chromium`. Facebook często pokazuje ekran
    logowania – wtedy scraper po prostu nic nie zwróci (bez wyjątku).
    """

    name = "facebook"

    def enabled(self) -> bool:
        return settings.enable_facebook and bool(settings.facebook_pages)

    def fetch(self) -> List[Deal]:
        deals: List[Deal] = []
        for page in settings.facebook_pages:
            url = page if page.startswith("http") else f"https://www.facebook.com/{page}"
            try:
                html = render_with_playwright(url, wait_ms=4000, mobile=True)
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")
                posts = soup.find_all(["article", "div"], attrs={"data-ft": True}) or soup.find_all("article")
                for post in posts[:20]:
                    text = post.get_text(" ", strip=True)
                    if len(text) < 30:
                        continue
                    link = post.find("a", href=re.compile(r"/(posts|story|permalink)"))
                    post_url = requests.compat.urljoin(url, link["href"]) if link else url
                    deal = text_to_deal(text[:1000], f"facebook:{page}", post_url)
                    if deal:
                        deals.append(deal)
            except Exception as exc:  # noqa: BLE001
                log.warning("Facebook: błąd dla %s: %s", page, exc)
        log.info("Facebook: znaleziono %d potencjalnych ofert", len(deals))
        return deals


# ====================================================================== #
# Playwright helper
# ====================================================================== #
def render_with_playwright(url: str, wait_ms: int = 2500, mobile: bool = False) -> Optional[str]:
    """Zwróć HTML strony po wyrenderowaniu w Chromium; None gdy niedostępne."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.debug("Playwright niezainstalowany – pomijam %s", url)
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ua = HEADERS["User-Agent"]
            if mobile:  # m.facebook.com jest prostszy do parsowania
                url = url.replace("www.facebook.com", "m.facebook.com")
                ua = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36"
            context = browser.new_context(user_agent=ua, locale="pl-PL")
            page = context.new_page()
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:  # noqa: BLE001
        log.warning("Playwright: nie udało się wyrenderować %s: %s", url, exc)
        return None


def all_web_scrapers() -> List[BaseScraper]:
    return [RssScraper(), AirlinePromoScraper(), FacebookScraper()]
