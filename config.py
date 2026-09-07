"""
Centralna konfiguracja aplikacji.

Wszystkie ustawienia czytane są ze zmiennych środowiskowych (plik .env lokalnie,
GitHub Secrets w GitHub Actions). Dzięki temu w repozytorium nie ma żadnych
tokenów ani haseł.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Wczytaj plik .env (jeśli istnieje) – w GitHub Actions zmienne przychodzą z Secrets.
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _env(name: str, default: str = "") -> str:
    """Pobierz zmienną środowiskową, usuwając białe znaki."""
    return os.getenv(name, default).strip()


def _env_list(name: str, default: str = "") -> List[str]:
    """Pobierz listę oddzieloną przecinkami, np. 'a,b,c' -> ['a','b','c']."""
    raw = _env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Wszystkie ustawienia w jednym obiekcie (config.settings)."""

    # --- Ogólne ---------------------------------------------------------
    dry_run: bool = _env_bool("DRY_RUN", False)          # nie wysyłaj maili, tylko loguj
    log_level: str = _env("LOG_LEVEL", "INFO")
    db_path: Path = DATA_DIR / _env("DB_FILE", "seen_deals.db")

    # Lotniska wylotu, które nas interesują (kody IATA).
    home_airports: List[str] = field(
        default_factory=lambda: _env_list(
            "HOME_AIRPORTS", "WAW,WMI,KRK,GDN,KTW,WRO,POZ,RZE,LUZ,SZZ,BZG,LCJ,BER"
        )
    )

    # --- Progi cenowe (PLN) --------------------------------------------
    max_price_europe: float = _env_float("MAX_PRICE_EUROPE_PLN", 60)
    max_price_longhaul: float = _env_float("MAX_PRICE_LONGHAUL_PLN", 1000)
    # Spadek względem mediany historycznej, który uznajemy za okazję (0.5 = -50%).
    price_drop_ratio: float = _env_float("PRICE_DROP_RATIO", 0.5)
    # Minimalna liczba obserwacji, zanim ufamy medianie.
    min_history_samples: int = _env_int("MIN_HISTORY_SAMPLES", 5)
    # Kurs EUR->PLN do zgrubnego przeliczania cen z postów (aktualizuj w .env).
    eur_pln: float = _env_float("EUR_PLN", 4.3)
    usd_pln: float = _env_float("USD_PLN", 3.9)
    gbp_pln: float = _env_float("GBP_PLN", 5.0)

    # --- API lotnicze ----------------------------------------------------
    kiwi_api_key: str = _env("KIWI_API_KEY")
    serpapi_key: str = _env("SERPAPI_KEY")
    duffel_token: str = _env("DUFFEL_ACCESS_TOKEN")
    # Kierunki sprawdzane przez API (kody IATA/miasta). Puste = "anywhere".
    api_destinations: List[str] = field(
        default_factory=lambda: _env_list(
            "API_DESTINATIONS", "BCN,LIS,ROM,LON,PAR,MAD,ATH,NYC,BKK,TYO,DXB"
        )
    )
    api_days_ahead: int = _env_int("API_DAYS_AHEAD", 90)  # okno wyszukiwania

    # --- Telegram --------------------------------------------------------
    telegram_api_id: int = _env_int("TELEGRAM_API_ID", 0)
    telegram_api_hash: str = _env("TELEGRAM_API_HASH")
    telegram_session: str = _env("TELEGRAM_SESSION")     # StringSession
    telegram_channels: List[str] = field(
        default_factory=lambda: _env_list(
            "TELEGRAM_CHANNELS", "fly4free,lowcymamutow,pepperpl,loterpl"
        )
    )
    telegram_lookback_minutes: int = _env_int("TELEGRAM_LOOKBACK_MINUTES", 60)

    # --- WWW / Facebook --------------------------------------------------
    rss_feeds: List[str] = field(
        default_factory=lambda: _env_list(
            "RSS_FEEDS",
            "https://www.fly4free.pl/feed/,https://loter.pl/feed/,https://www.pepper.pl/rss/grupa/podroze",
        )
    )
    facebook_pages: List[str] = field(default_factory=lambda: _env_list("FACEBOOK_PAGES", ""))
    enable_facebook: bool = _env_bool("ENABLE_FACEBOOK", False)
    enable_airline_pages: bool = _env_bool("ENABLE_AIRLINE_PAGES", True)

    # --- E-mail ----------------------------------------------------------
    smtp_host: str = _env("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _env_int("SMTP_PORT", 587)
    smtp_user: str = _env("SMTP_USER")
    smtp_password: str = _env("SMTP_PASSWORD")
    email_from: str = _env("EMAIL_FROM") or _env("SMTP_USER")
    email_to: List[str] = field(default_factory=lambda: _env_list("EMAIL_TO"))
    sendgrid_key: str = _env("SENDGRID_API_KEY")


settings = Settings()
