"""
Scraper Telegrama oparty na Telethon.

Dwa tryby pracy:
  * fetch()  – tryb jednorazowy (GitHub Actions): pobiera wiadomości z ostatnich
               TELEGRAM_LOOKBACK_MINUTES z każdego kanału i zwraca je jako Deal.
               Pamięta ID ostatniej przeczytanej wiadomości w bazie (state).
  * listen() – tryb ciągły (VPS / Raspberry Pi): nasłuchuje nowych wiadomości
               w czasie rzeczywistym i wywołuje callback dla każdej z nich.

Logowanie odbywa się przez StringSession (TELEGRAM_SESSION) – jeden ciąg
znaków wygenerowany raz lokalnie skryptem `python -m scrapers.telegram_scraper --login`.
Dzięki temu w GitHub Actions nie trzeba wpisywać kodu SMS.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, List, Optional

from config import settings
from scrapers.base import (
    BaseScraper,
    Deal,
    extract_dates,
    extract_first_url,
    extract_price_pln,
)
from utils import airports
from utils.deduplicator import Deduplicator
from utils.logger import get_logger

log = get_logger(__name__)

try:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    TELETHON_AVAILABLE = True
except ImportError:  # pragma: no cover
    TELETHON_AVAILABLE = False


def message_to_deal(text: str, channel: str, url: str) -> Optional[Deal]:
    """
    Zamień treść posta na Deal. Zwraca None, gdy post nie wygląda na ofertę
    z polskiego lotniska (brak ceny lub brak lotniska wylotu).
    """
    if not text:
        return None
    origin = airports.find_origin(text, settings.home_airports)
    if not origin:
        return None
    price = extract_price_pln(text, settings.eur_pln, settings.usd_pln, settings.gbp_pln)
    if price is None:
        return None
    dest = airports.find_destination(text)
    date_from, date_to = extract_dates(text)
    booking = extract_first_url(text) or url
    return Deal(
        origin=origin,
        destination=dest[0] if dest else "???",
        country=dest[1] if dest else None,
        price_pln=price,
        source=f"telegram:{channel}",
        url=booking,
        date_from=date_from,
        date_to=date_to,
        description=text.strip()[:400],
    )


class TelegramScraper(BaseScraper):
    name = "telegram"

    def enabled(self) -> bool:
        ok = TELETHON_AVAILABLE and bool(
            settings.telegram_api_id and settings.telegram_api_hash and settings.telegram_session
        )
        if not ok:
            log.info("Telegram: brak konfiguracji (API_ID/API_HASH/SESSION) – pomijam")
        return ok

    def __init__(self, dedup: Optional[Deduplicator] = None):
        self.dedup = dedup

    def _client(self) -> "TelegramClient":
        return TelegramClient(
            StringSession(settings.telegram_session),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

    # ------------------------------------------------------------------ #
    # Tryb jednorazowy
    # ------------------------------------------------------------------ #
    def fetch(self) -> List[Deal]:
        try:
            return asyncio.run(self._fetch_async())
        except Exception as exc:  # noqa: BLE001
            log.error("Telegram: błąd pobierania wiadomości: %s", exc)
            return []

    async def _fetch_async(self) -> List[Deal]:
        deals: List[Deal] = []
        since = datetime.now(timezone.utc) - timedelta(minutes=settings.telegram_lookback_minutes)
        async with self._client() as client:
            for channel in settings.telegram_channels:
                try:
                    entity = await client.get_entity(channel)
                    last_id_raw = self.dedup.get_state(f"tg_last_id:{channel}") if self.dedup else None
                    last_id = int(last_id_raw) if last_id_raw else 0
                    newest_id = last_id
                    async for msg in client.iter_messages(entity, limit=50):
                        if msg.id <= last_id:
                            break
                        if msg.date and msg.date < since and last_id == 0:
                            break  # pierwsze uruchomienie – tylko świeże posty
                        newest_id = max(newest_id, msg.id)
                        text = msg.message or ""
                        url = f"https://t.me/{channel.lstrip('@')}/{msg.id}"
                        deal = message_to_deal(text, channel.lstrip("@"), url)
                        if deal:
                            deals.append(deal)
                    if self.dedup and newest_id > last_id:
                        self.dedup.set_state(f"tg_last_id:{channel}", str(newest_id))
                except Exception as exc:  # noqa: BLE001 – np. kanał prywatny / nie istnieje
                    log.warning("Telegram: nie udało się odczytać %s: %s", channel, exc)
        log.info("Telegram: znaleziono %d potencjalnych ofert", len(deals))
        return deals

    # ------------------------------------------------------------------ #
    # Tryb ciągły (daemon)
    # ------------------------------------------------------------------ #
    async def listen(self, on_deal: Callable[[Deal], Awaitable[None]]) -> None:
        """Nasłuchuj nowych wiadomości i wołaj `on_deal` dla każdej oferty."""
        client = self._client()

        @client.on(events.NewMessage(chats=settings.telegram_channels))
        async def handler(event):  # noqa: ANN001
            try:
                chat = await event.get_chat()
                username = getattr(chat, "username", None) or str(event.chat_id)
                url = f"https://t.me/{username}/{event.message.id}"
                deal = message_to_deal(event.message.message or "", username, url)
                if deal:
                    await on_deal(deal)
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram: błąd obsługi wiadomości: %s", exc)

        await client.start()
        log.info("Telegram: nasłuchuję kanałów %s", ", ".join(settings.telegram_channels))
        await client.run_until_disconnected()


# ---------------------------------------------------------------------- #
# Pomocnik: jednorazowe logowanie i wygenerowanie StringSession
# ---------------------------------------------------------------------- #
def interactive_login() -> None:
    """
    Uruchom lokalnie:  python -m scrapers.telegram_scraper --login
    Podaj numer telefonu i kod z Telegrama; skrypt wypisze TELEGRAM_SESSION,
    który wklejasz do .env / GitHub Secrets.
    """
    if not TELETHON_AVAILABLE:
        print("Zainstaluj telethon: pip install telethon")
        return
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        print("Ustaw TELEGRAM_API_ID i TELEGRAM_API_HASH w .env (z https://my.telegram.org)")
        return
    with TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash) as client:
        print("\n=== Skopiuj poniższy ciąg do TELEGRAM_SESSION ===\n")
        print(client.session.save())
        print("\n=================================================\n")


if __name__ == "__main__":
    if "--login" in sys.argv:
        interactive_login()
    else:
        for d in TelegramScraper(Deduplicator(settings.db_path)).fetch():
            print(d.short())
