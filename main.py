"""
error-fare-hunter – główny punkt wejścia.

Tryby uruchomienia:
  python main.py            – jedno przejście po wszystkich źródłach (GitHub Actions / cron)
  python main.py --daemon   – pętla: co N minut skanuje API/WWW, a Telegram nasłuchuje na żywo
  python main.py --dry-run  – jak wyżej, ale bez wysyłania maili (tylko logi)
  python main.py --only api|telegram|web – uruchom tylko wybrane źródło

Przepływ:
  scrapery -> lista Deal -> PriceAnalyzer (geo + cena + dedup) -> EmailNotifier -> mark_seen
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import List

from config import settings
from analyzers.price_analyzer import PriceAnalyzer
from notifiers.email_notifier import EmailNotifier
from scrapers.api_scraper import all_api_scrapers
from scrapers.base import BaseScraper, Deal
from scrapers.telegram_scraper import TelegramScraper
from scrapers.web_scraper import all_web_scrapers
from utils.deduplicator import Deduplicator
from utils.logger import get_logger

log = get_logger("main")


def collect(scrapers: List[BaseScraper]) -> List[Deal]:
    """Uruchom po kolei scrapery; awaria jednego nie przerywa pozostałych."""
    deals: List[Deal] = []
    for scraper in scrapers:
        try:
            if not scraper.enabled():
                log.info("Pomijam źródło %s (brak konfiguracji / wyłączone)", scraper.name)
                continue
            log.info("Źródło %s: start", scraper.name)
            found = scraper.fetch()
            deals.extend(found)
        except Exception as exc:  # noqa: BLE001
            log.error("Źródło %s zakończyło się błędem: %s", scraper.name, exc, exc_info=True)
    return deals


def process(deals: List[Deal], analyzer: PriceAnalyzer, notifier: EmailNotifier, dedup: Deduplicator) -> int:
    """Przefiltruj, wyślij powiadomienie, zapisz do bazy. Zwraca liczbę wysłanych okazji."""
    if not deals:
        log.info("Brak ofert do analizy")
        return 0
    log.info("Analizuję %d ofert", len(deals))
    hits = analyzer.analyze(deals)
    if not hits:
        log.info("Brak nowych okazji")
        return 0

    hits.sort(key=lambda d: d.price_pln)
    for d in hits:
        log.info("OKAZJA: %s – %s", d.short(), d.reason)

    sent = notifier.send(hits) if notifier.enabled() else False
    if sent or settings.dry_run:
        # Oznaczamy jako widziane dopiero po udanej wysyłce – jeśli SMTP padnie,
        # oferta wróci przy następnym uruchomieniu.
        for d in hits:
            dedup.mark_seen(d.fingerprint(), d.route, d.price_pln, d.source, d.url)
        return len(hits)
    log.error("Nie udało się wysłać powiadomienia – oferty NIE zostały oznaczone jako widziane")
    return 0


def build_scrapers(only: str | None, dedup: Deduplicator) -> List[BaseScraper]:
    scrapers: List[BaseScraper] = []
    if only in (None, "api"):
        scrapers += all_api_scrapers()
    if only in (None, "telegram"):
        scrapers.append(TelegramScraper(dedup))
    if only in (None, "web"):
        scrapers += all_web_scrapers()
    return scrapers


def _write_last_run(**payload) -> None:
    """data/last_run.json – czyta go scripts/export_state.py (status na stronie telefonu)."""
    import json
    from datetime import datetime, timezone
    try:
        path = settings.db_path.parent / "last_run.json"
        payload.setdefault("time", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Nie udało się zapisać last_run.json: %s", exc)


def run_once(only: str | None = None) -> int:
    dedup = Deduplicator(settings.db_path)
    dedup.purge_old()
    analyzer = PriceAnalyzer(dedup)
    notifier = EmailNotifier()
    scrapers = build_scrapers(only, dedup)
    try:
        deals = collect(scrapers)
        sent = process(deals, analyzer, notifier, dedup)
        _write_last_run(mode="scan", ok=True, sent=sent, found=len(deals),
                        sources=[s.name for s in scrapers if s.enabled()])
        return sent
    except Exception as exc:
        _write_last_run(mode="scan", ok=False, sent=0, error=str(exc))
        raise


def send_test_email() -> bool:
    """Wysyła testową wiadomość (przycisk „Wyślij testowego maila” na stronie)."""
    deal = Deal("WAW", "BCN", 49, "test", "https://www.google.com/travel/flights", "2026-10-10", "2026-10-14",
                "Testowa wiadomość – konfiguracja e-maila działa!", country="ES", reason="test konfiguracji")
    notifier = EmailNotifier()
    if not notifier.enabled():
        _write_last_run(mode="test_email", ok=False, error="brak adresu Gmail lub hasła aplikacji")
        return False
    ok = notifier.send([deal])
    _write_last_run(mode="test_email", ok=ok, error=None if ok else "SMTP odrzucił logowanie – sprawdź hasło aplikacji")
    return ok


async def run_daemon(interval_minutes: int) -> None:
    """Tryb ciągły: Telegram na żywo + cykliczny skan API/WWW."""
    dedup = Deduplicator(settings.db_path)
    analyzer = PriceAnalyzer(dedup)
    notifier = EmailNotifier()
    telegram = TelegramScraper(dedup)

    async def on_telegram_deal(deal: Deal) -> None:
        process([deal], analyzer, notifier, dedup)

    async def periodic_scan() -> None:
        while True:
            try:
                deals = await asyncio.to_thread(collect, all_api_scrapers() + all_web_scrapers())
                process(deals, analyzer, notifier, dedup)
                dedup.purge_old()
            except Exception as exc:  # noqa: BLE001
                log.error("Błąd cyklicznego skanu: %s", exc, exc_info=True)
            await asyncio.sleep(interval_minutes * 60)

    tasks = [asyncio.create_task(periodic_scan())]
    if telegram.enabled():
        tasks.append(asyncio.create_task(telegram.listen(on_telegram_deal)))
    await asyncio.gather(*tasks)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Łowca error fares z polskich lotnisk")
    parser.add_argument("--daemon", action="store_true", help="tryb ciągły (VPS)")
    parser.add_argument("--interval", type=int, default=30, help="minuty między skanami w trybie daemon")
    parser.add_argument("--dry-run", action="store_true", help="nie wysyłaj maili")
    parser.add_argument("--only", choices=["api", "telegram", "web"], help="tylko jedno źródło")
    parser.add_argument("--test-email", action="store_true", help="wyślij testowego maila i zakończ")
    args = parser.parse_args(argv)

    if args.dry_run:
        settings.dry_run = True
        log.info("DRY RUN – maile nie będą wysyłane")

    started = time.time()
    try:
        if args.test_email:
            return 0 if send_test_email() else 1
        if args.daemon:
            asyncio.run(run_daemon(args.interval))
        else:
            sent = run_once(args.only)
            log.info("Zakończono w %.1fs, wysłano %d okazji", time.time() - started, sent)
    except KeyboardInterrupt:
        log.info("Przerwano przez użytkownika")
    except Exception as exc:  # noqa: BLE001
        log.critical("Nieoczekiwany błąd: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
