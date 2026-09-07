"""Szybkie testy logiki bez dostępu do sieci: uruchom `pytest`."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DRY_RUN"] = "1"

from scrapers.base import Deal, extract_dates, extract_price_pln  # noqa: E402
from scrapers.telegram_scraper import message_to_deal  # noqa: E402
from utils import airports  # noqa: E402
from utils.deduplicator import Deduplicator  # noqa: E402
from analyzers.price_analyzer import PriceAnalyzer  # noqa: E402


def test_price_extraction_picks_lowest_and_converts():
    assert extract_price_pln("Loty do Rzymu od 39 zł (normalnie 450 zł)", 4.3, 3.9, 5.0) == 39
    assert extract_price_pln("Barcelona za 19€ w obie strony", 4.3, 3.9, 5.0) == 19 * 4.3
    assert extract_price_pln("Bangkok za 1 299 zł", 4.3, 3.9, 5.0) == 1299
    assert extract_price_pln("Brak ceny w tym poście z 2026 roku", 4.3, 3.9, 5.0) is None


def test_airport_detection_polish_declension():
    homes = ["WAW", "KRK", "GDN"]
    assert airports.find_origin("Loty z Krakowa do Lizbony", homes) == "KRK"
    assert airports.find_origin("WAW-BCN za 99 zł", homes) == "WAW"
    assert airports.find_origin("Z Gdańska na Maderę", homes) == "GDN"
    assert airports.find_origin("Loty z Berlina do Tokio", homes) is None


def test_destination_and_longhaul():
    code, country = airports.find_destination("Tanie loty do Nowego Jorku z Warszawy")
    assert code == "NYC" and airports.is_longhaul(country)
    code, country = airports.find_destination("Weekend w Lizbonie")
    assert code == "LIS" and not airports.is_longhaul(country)


def test_dates():
    assert extract_dates("Terminy: 12.10-19.10.2026") == ("2026-10-12", "2026-10-19")
    assert extract_dates("Wylot w listopadzie")[0] == "listopadzie"


def test_message_to_deal_and_analyzer(tmp_path):
    text = "🔥 Error fare! Warszawa – Bangkok za 899 zł w obie strony, terminy 03.11-17.11.2026 https://kiwi.com/x"
    deal = message_to_deal(text, "fly4free", "https://t.me/fly4free/1")
    assert deal and deal.origin == "WAW" and deal.destination == "BKK" and deal.price_pln == 899
    assert deal.url == "https://kiwi.com/x"

    dedup = Deduplicator(tmp_path / "t.db")
    analyzer = PriceAnalyzer(dedup)
    hits = analyzer.analyze([deal])
    assert len(hits) == 1 and "daleki dystans" in hits[0].reason

    # Po oznaczeniu jako widziane – nie wraca
    dedup.mark_seen(deal.fingerprint(), deal.route, deal.price_pln, deal.source, deal.url)
    assert analyzer.analyze([deal]) == []


def test_deviation_from_median(tmp_path):
    dedup = Deduplicator(tmp_path / "t.db")
    for p in (800, 820, 790, 850, 810):
        dedup.record_price("WAW-BCN", p)
    cheap = Deal("WAW", "BCN", 300, "kiwi", "https://kiwi.com/y", "2026-10-01", "2026-10-08", country="ES")
    normal = Deal("WAW", "BCN", 780, "kiwi", "https://kiwi.com/z", "2026-10-01", "2026-10-08", country="ES")
    hits = PriceAnalyzer(dedup).analyze([cheap, normal])
    assert [h.destination for h in hits] == ["BCN"] and "mediana" in hits[0].reason
