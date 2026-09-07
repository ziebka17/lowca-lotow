"""
Logowanie do Telegrama sterowane z telefonu – uruchamiane przez workflow
`telegram-login.yml` (workflow_dispatch z telefonu).

Krok 1  ACTION=send_code  PHONE=+48...
        -> wysyła kod do aplikacji Telegram, zapisuje tymczasową sesję jako sekret TG_PENDING,
           status.json: step=code_sent
Krok 2  ACTION=sign_in    CODE=12345  [PASSWORD=hasło 2FA]
        -> kończy logowanie, zapisuje sekret TELEGRAM_SESSION, usuwa TG_PENDING,
           zapisuje data/channels.json z listą kanałów użytkownika,
           status.json: step=logged_in
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gh_api import GitHub, write_status  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"


async def send_code(api_id: int, api_hash: str, phone: str) -> tuple[str, str]:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        return client.session.save(), sent.phone_code_hash
    finally:
        await client.disconnect()


async def resend_sms(api_id: int, api_hash: str, pending: dict) -> str:
    """Poproś Telegram o ponowne wysłanie kodu inną drogą (zwykle SMS)."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(pending["session"]), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.resend_code_request(pending["phone"], pending["hash"])
        return sent.phone_code_hash
    finally:
        await client.disconnect()


async def sign_in(api_id: int, api_hash: str, pending: dict, code: str, password: str) -> tuple[str, list]:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession
    from telethon.tl.types import Channel
    client = TelegramClient(StringSession(pending["session"]), api_id, api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=pending["phone"], code=code, phone_code_hash=pending["hash"])
        except SessionPasswordNeededError:
            if not password:
                raise RuntimeError("needs_password")
            await client.sign_in(password=password)
        channels = []
        async for d in client.iter_dialogs():
            if isinstance(d.entity, Channel) and getattr(d.entity, "username", None):
                channels.append({"username": d.entity.username, "name": d.name})
        return client.session.save(), channels
    finally:
        await client.disconnect()


def main() -> int:
    action = os.environ.get("ACTION", "")
    gh = GitHub()
    try:
        api_id = int(os.environ.get("TELEGRAM_API_ID") or 0)
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")
        if not (api_id and api_hash):
            write_status("telegram", False, "Brak api_id / api_hash – uzupełnij je na stronie i zapisz.")
            return 0

        if action == "send_code":
            phone = os.environ.get("PHONE", "").strip()
            session, code_hash = asyncio.run(send_code(api_id, api_hash, phone))
            gh.set_secret("TG_PENDING", json.dumps({"session": session, "hash": code_hash, "phone": phone}))
            write_status("code_sent", True, "Kod wysłany do aplikacji Telegram. Wpisz go na stronie.")

        elif action == "resend_sms":
            pending = json.loads(os.environ.get("TG_PENDING") or "{}")
            if not pending:
                write_status("telegram", False, "Najpierw kliknij „Wyślij kod”.")
                return 0
            pending["hash"] = asyncio.run(resend_sms(api_id, api_hash, pending))
            gh.set_secret("TG_PENDING", json.dumps(pending))
            write_status("code_sent", True, "Kod wysłany ponownie (SMS-em lub połączeniem). Wpisz go na stronie.")

        elif action == "sign_in":
            pending = json.loads(os.environ.get("TG_PENDING") or "{}")
            if not pending:
                write_status("telegram", False, "Najpierw wyślij kod (krok 1).")
                return 0
            code = os.environ.get("CODE", "").strip().replace(" ", "")
            password = os.environ.get("PASSWORD", "")
            try:
                session, channels = asyncio.run(sign_in(api_id, api_hash, pending, code, password))
            except RuntimeError as exc:
                if str(exc) == "needs_password":
                    write_status("needs_password", False,
                                 "Konto ma hasło dwuetapowe Telegrama – wpisz kod i hasło, potem 'Zaloguj' ponownie.")
                    return 0
                raise
            gh.set_secret("TELEGRAM_SESSION", session)
            gh.delete_secret("TG_PENDING")
            DATA.mkdir(exist_ok=True)
            (DATA / "channels.json").write_text(json.dumps(channels, ensure_ascii=False, indent=2), encoding="utf-8")
            write_status("logged_in", True, f"Zalogowano do Telegrama. Znaleziono {len(channels)} kanałów – zaznacz, które śledzić.")
        else:
            write_status("telegram", False, f"Nieznana akcja: {action}")
    except Exception as exc:  # noqa: BLE001
        write_status("telegram", False, f"Błąd Telegrama: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
