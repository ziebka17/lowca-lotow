"""Wspólna konfiguracja logowania dla wszystkich modułów."""
from __future__ import annotations

import logging
import sys

from config import settings

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Zwróć logger o podanej nazwie (konfiguracja globalna tylko raz)."""
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stdout,
        )
        # Wycisz gadatliwe biblioteki
        for noisy in ("httpx", "telethon", "urllib3", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        _CONFIGURED = True
    return logging.getLogger(name)
