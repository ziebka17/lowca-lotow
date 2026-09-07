"""
Panel WWW – lokalna strona w przeglądarce do obsługi łowcy (bez Terminala).

Uruchomienie:  START.command  (Mac)  /  START.bat (Windows)  /  python app.py
Otwiera http://127.0.0.1:8765 w przeglądarce.

Endpointy API (wywoływane przez templates/index.html):
  GET  /api/state                – ustawienia (hasła zamaskowane), status, okazje, log
  POST /api/settings             – zapis ustawień do .env
  POST /api/test-email           – wysyłka testowego maila
  POST /api/telegram/send-code   – krok 1 logowania (numer telefonu)
  POST /api/telegram/sign-in     – krok 2 (kod z aplikacji, ewentualnie hasło 2FA)
  GET  /api/telegram/channels    – lista kanałów konta
  POST /api/scan                 – jednorazowy skan w tle
  POST /api/background/start|stop – tryb ciągły (launchd na macOS / proces w tle)
  POST /api/github/deploy        – wysyłka do GitHub Actions przez token (REST API)
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402
from flask import Flask, jsonify, render_template, request  # noqa: E402

ENV_FILE = BASE / ".env"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
LOG_FILE = DATA / "hunter.log"
PID_FILE = DATA / "daemon.pid"
PLIST = Path.home() / "Library" / "LaunchAgents" / "pl.errorfare.hunter.plist"
PY = sys.executable
PORT = int(os.getenv("PANEL_PORT", "8765"))

SECRET_KEYS = {"SMTP_PASSWORD", "TELEGRAM_API_HASH", "TELEGRAM_SESSION", "SENDGRID_API_KEY",
               "KIWI_API_KEY", "SERPAPI_KEY", "DUFFEL_ACCESS_TOKEN"}
EDITABLE_KEYS = [
    "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "SMTP_HOST", "SMTP_PORT",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_CHANNELS",
    "HOME_AIRPORTS", "MAX_PRICE_EUROPE_PLN", "MAX_PRICE_LONGHAUL_PLN", "PRICE_DROP_RATIO",
    "RSS_FEEDS", "ENABLE_AIRLINE_PAGES", "ENABLE_FACEBOOK", "FACEBOOK_PAGES",
    "SERPAPI_KEY", "KIWI_API_KEY", "DRY_RUN",
]
DEFAULTS = {
    "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
    "HOME_AIRPORTS": "WAW,WMI,KRK,GDN,KTW,WRO,POZ,BER",
    "MAX_PRICE_EUROPE_PLN": "60", "MAX_PRICE_LONGHAUL_PLN": "1000", "PRICE_DROP_RATIO": "0.5",
    "TELEGRAM_CHANNELS": "fly4free,lowcymamutow,pepperpl,loterpl",
    "RSS_FEEDS": "https://www.fly4free.pl/feed/,https://loter.pl/feed/,https://www.pepper.pl/rss/grupa/podroze",
    "ENABLE_AIRLINE_PAGES": "0", "ENABLE_FACEBOOK": "0", "DRY_RUN": "0",
}

app = Flask(__name__, template_folder=str(BASE / "templates"))
app.config["JSON_AS_ASCII"] = False
_lock = threading.Lock()
_tg_pending: dict = {}          # stan między krokiem 1 i 2 logowania do Telegrama
_scan_thread: threading.Thread | None = None


# ---------------------------------------------------------------- .env + settings

def load_env() -> dict:
    data = dict(DEFAULTS)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def save_env(data: dict) -> None:
    lines = ["# Wygenerowane przez panel (app.py). Nie wrzucaj tego pliku na GitHub – jest w .gitignore."]
    for k, v in data.items():
        if v is None:
            continue
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reload_settings()


def reload_settings() -> None:
    """Przeładuj config.settings w miejscu, żeby już zaimportowane moduły widziały nowe wartości."""
    load_dotenv(ENV_FILE, override=True)
    import config
    old = config.settings
    importlib.reload(config)
    old.__dict__.update(config.settings.__dict__)
    config.settings = old


def setup_logging() -> None:
    import logging
    from utils.logger import get_logger
    get_logger("panel")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(fh)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # bez logów każdego żądania HTTP
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None


def log(msg: str) -> None:
    import logging
    logging.getLogger("panel").info(msg)


# ---------------------------------------------------------------- status

def daemon_running() -> bool:
    if platform.system() == "Darwin" and PLIST.exists():
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
        return "pl.errorfare.hunter" in out
    if PID_FILE.exists():
        try:
            os.kill(int(PID_FILE.read_text()), 0)
            return True
        except (OSError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    return False


def recent_deals(limit: int = 30) -> list:
    import sqlite3
    db = DATA / "seen_deals.db"
    if not db.exists():
        return []
    try:
        rows = sqlite3.connect(db).execute(
            "SELECT seen_at, route, price_pln, source, url FROM seen_deals ORDER BY seen_at DESC LIMIT ?", (limit,)
        ).fetchall()
    except Exception:
        return []
    return [{"seen_at": r[0][:16].replace("T", " "), "route": r[1], "price": r[2], "source": r[3], "url": r[4]} for r in rows]


def log_tail(lines: int = 60) -> str:
    if not LOG_FILE.exists():
        return ""
    try:
        return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:])
    except Exception:
        return ""


# ---------------------------------------------------------------- routes: strona + stan

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    env = load_env()
    shown = {k: ("••••••" if (k in SECRET_KEYS and env.get(k)) else env.get(k, "")) for k in EDITABLE_KEYS}
    return jsonify(
        settings=shown,
        status={
            "email_ok": bool(env.get("SMTP_USER") and env.get("SMTP_PASSWORD")),
            "telegram_ok": bool(env.get("TELEGRAM_SESSION")),
            "daemon": daemon_running(),
            "scanning": bool(_scan_thread and _scan_thread.is_alive()),
            "mac": platform.system() == "Darwin",
        },
        deals=recent_deals(),
        log=log_tail(),
    )


@app.post("/api/settings")
def api_settings():
    incoming = request.get_json(force=True) or {}
    env = load_env()
    for k in EDITABLE_KEYS:
        if k not in incoming:
            continue
        v = str(incoming[k]).strip()
        if k in SECRET_KEYS and v == "••••••":
            continue  # nie nadpisuj zamaskowanej wartości
        if k == "SMTP_PASSWORD":
            v = v.replace(" ", "")
        env[k] = v
    env.setdefault("EMAIL_FROM", env.get("SMTP_USER", ""))
    env["EMAIL_FROM"] = env.get("SMTP_USER", "")
    if not env.get("EMAIL_TO"):
        env["EMAIL_TO"] = env.get("SMTP_USER", "")
    save_env(env)
    return jsonify(ok=True)


# ---------------------------------------------------------------- e-mail

@app.post("/api/test-email")
def api_test_email():
    try:
        reload_settings()
        from notifiers.email_notifier import EmailNotifier
        from scrapers.base import Deal
        deal = Deal("WAW", "BCN", 49, "test", "https://www.google.com/travel/flights", "2026-10-10", "2026-10-14",
                    "Testowa wiadomość z panelu – konfiguracja e-maila działa!", country="ES", reason="test konfiguracji")
        notifier = EmailNotifier()
        if not notifier.enabled():
            return jsonify(ok=False, error="Uzupełnij adres Gmail, hasło aplikacji i adres odbiorcy, potem zapisz.")
        ok = notifier.send([deal])
        return jsonify(ok=ok, error=None if ok else "Wysyłka nie powiodła się – sprawdź hasło aplikacji (szczegóły w logu).")
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc))


# ---------------------------------------------------------------- telegram

def _tg_creds():
    env = load_env()
    try:
        return int(env.get("TELEGRAM_API_ID") or 0), env.get("TELEGRAM_API_HASH", "")
    except ValueError:
        return 0, ""


@app.post("/api/telegram/send-code")
def api_tg_send_code():
    phone = (request.get_json(force=True) or {}).get("phone", "").strip()
    api_id, api_hash = _tg_creds()
    if not (api_id and api_hash):
        return jsonify(ok=False, error="Najpierw wpisz i zapisz api_id oraz api_hash z my.telegram.org.")
    if not phone.startswith("+"):
        return jsonify(ok=False, error="Numer w formacie międzynarodowym, np. +48600100200")

    async def go():
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        session = client.session.save()
        await client.disconnect()
        return session, sent.phone_code_hash

    try:
        session, code_hash = asyncio.run(go())
        _tg_pending.update(session=session, phone=phone, hash=code_hash)
        return jsonify(ok=True)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"Telegram: {exc}")


@app.post("/api/telegram/sign-in")
def api_tg_sign_in():
    body = request.get_json(force=True) or {}
    code = body.get("code", "").strip().replace(" ", "")
    password = body.get("password", "")
    if not _tg_pending:
        return jsonify(ok=False, error="Najpierw wyślij kod (krok 1).")
    api_id, api_hash = _tg_creds()

    async def go():
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(_tg_pending["session"]), api_id, api_hash)
        await client.connect()
        try:
            try:
                await client.sign_in(phone=_tg_pending["phone"], code=code, phone_code_hash=_tg_pending["hash"])
            except SessionPasswordNeededError:
                if not password:
                    return None, "needs_password"
                await client.sign_in(password=password)
            return client.session.save(), None
        finally:
            await client.disconnect()

    try:
        session, err = asyncio.run(go())
        if err == "needs_password":
            return jsonify(ok=False, needs_password=True, error="Konto ma hasło dwuetapowe – wpisz je poniżej.")
        env = load_env()
        env["TELEGRAM_SESSION"] = session
        save_env(env)
        _tg_pending.clear()
        log("Telegram: zalogowano")
        return jsonify(ok=True)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"Telegram: {exc}")


@app.get("/api/telegram/channels")
def api_tg_channels():
    env = load_env()
    api_id, api_hash = _tg_creds()
    session = env.get("TELEGRAM_SESSION")
    if not session:
        return jsonify(ok=False, error="Najpierw zaloguj się do Telegrama.")

    async def go():
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.types import Channel
        out = []
        async with TelegramClient(StringSession(session), api_id, api_hash) as client:
            async for d in client.iter_dialogs():
                if isinstance(d.entity, Channel) and getattr(d.entity, "username", None):
                    out.append({"username": d.entity.username, "name": d.name})
        return out

    try:
        chans = asyncio.run(go())
        selected = set(env.get("TELEGRAM_CHANNELS", "").split(","))
        for c in chans:
            c["selected"] = c["username"] in selected
        return jsonify(ok=True, channels=chans)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"Telegram: {exc}")


@app.post("/api/telegram/logout")
def api_tg_logout():
    env = load_env()
    env["TELEGRAM_SESSION"] = ""
    save_env(env)
    return jsonify(ok=True)


# ---------------------------------------------------------------- skan + tło

@app.post("/api/scan")
def api_scan():
    global _scan_thread
    with _lock:
        if _scan_thread and _scan_thread.is_alive():
            return jsonify(ok=False, error="Skan już trwa.")

        def worker():
            try:
                reload_settings()
                import main as hunter
                sent = hunter.run_once()
                log(f"Skan zakończony – wysłano {sent} okazji")
            except Exception as exc:  # noqa: BLE001
                log(f"Skan zakończony błędem: {exc}")

        _scan_thread = threading.Thread(target=worker, daemon=True)
        _scan_thread.start()
    return jsonify(ok=True)


@app.post("/api/background/start")
def api_bg_start():
    if platform.system() == "Darwin":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>pl.errorfare.hunter</string>
  <key>ProgramArguments</key><array>
    <string>{PY}</string><string>{BASE / 'main.py'}</string><string>--daemon</string><string>--interval</string><string>20</string>
  </array>
  <key>WorkingDirectory</key><string>{BASE}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{LOG_FILE}</string>
  <key>StandardErrorPath</key><string>{LOG_FILE}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        res = subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True, text=True)
        if res.returncode != 0:
            return jsonify(ok=False, error=res.stderr.strip() or "launchctl error")
    else:
        if daemon_running():
            return jsonify(ok=True)
        logf = open(LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen([PY, str(BASE / "main.py"), "--daemon", "--interval", "20"],
                                stdout=logf, stderr=subprocess.STDOUT, cwd=BASE)
        PID_FILE.write_text(str(proc.pid))
    log("Tryb w tle włączony")
    return jsonify(ok=True)


@app.post("/api/background/stop")
def api_bg_stop():
    if platform.system() == "Darwin" and PLIST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink(missing_ok=True)
    if PID_FILE.exists():
        try:
            os.kill(int(PID_FILE.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        PID_FILE.unlink(missing_ok=True)
    log("Tryb w tle zatrzymany")
    return jsonify(ok=True)


# ---------------------------------------------------------------- GitHub (REST API, token)

GH_API = "https://api.github.com"
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
SKIP_FILES = {".env", "hunter.log", "daemon.pid", "seen_deals.db", ".DS_Store"}


def _project_files() -> list[tuple[str, bytes]]:
    files = []
    for path in BASE.rglob("*"):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.parts) or path.name in SKIP_FILES or path.suffix in {".env", ".db", ".log", ".pid"}:
            continue
        rel = path.relative_to(BASE).as_posix()
        if rel == "workflow_template.yml":
            continue
        files.append((rel, path.read_bytes()))
    template = BASE / "workflow_template.yml"
    if template.exists() and not (BASE / ".github/workflows/checker.yml").exists():
        files.append((".github/workflows/checker.yml", template.read_bytes()))
    return files


@app.post("/api/github/deploy")
def api_github_deploy():
    import requests
    body = request.get_json(force=True) or {}
    token = body.get("token", "").strip()
    name = (body.get("repo") or "error-fare-hunter").strip()
    if not token:
        return jsonify(ok=False, error="Wklej token GitHub (repo + workflow).")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    steps = []
    try:
        r = s.get(f"{GH_API}/user", timeout=20)
        if r.status_code != 200:
            return jsonify(ok=False, error="Token odrzucony przez GitHub – sprawdź, czy ma zakresy repo i workflow.")
        owner = r.json()["login"]
        repo = f"{owner}/{name}"

        # 1. repozytorium
        if s.get(f"{GH_API}/repos/{repo}", timeout=20).status_code == 404:
            r = s.post(f"{GH_API}/user/repos", json={"name": name, "private": True, "auto_init": True,
                                                     "description": "Łowca error fares z polskich lotnisk"}, timeout=30)
            if r.status_code not in (200, 201):
                return jsonify(ok=False, error=f"Nie udało się utworzyć repo: {r.json().get('message')}")
            steps.append(f"Utworzono prywatne repozytorium {repo}")
        branch = s.get(f"{GH_API}/repos/{repo}", timeout=20).json().get("default_branch", "main")

        # 2. pliki (Contents API – jeden commit na plik)
        uploaded = 0
        for rel, content in _project_files():
            url = f"{GH_API}/repos/{repo}/contents/{rel}"
            existing = s.get(url, params={"ref": branch}, timeout=20)
            payload = {"message": f"update {rel}", "content": base64.b64encode(content).decode(), "branch": branch}
            if existing.status_code == 200:
                if existing.json().get("sha") and base64.b64decode(existing.json().get("content", "").encode()) == content:
                    continue  # bez zmian
                payload["sha"] = existing.json()["sha"]
            r = s.put(url, json=payload, timeout=30)
            if r.status_code not in (200, 201):
                return jsonify(ok=False, error=f"Błąd wysyłania {rel}: {r.json().get('message')}", steps=steps)
            uploaded += 1
        steps.append(f"Wysłano {uploaded} plików")

        # 3. sekrety (szyfrowanie kluczem publicznym repo – PyNaCl)
        from nacl import encoding, public
        pk = s.get(f"{GH_API}/repos/{repo}/actions/secrets/public-key", timeout=20).json()
        box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
        env = load_env()
        count = 0
        for k, v in env.items():
            if not v or k in {"DRY_RUN", "LOG_LEVEL"}:
                continue
            enc = base64.b64encode(box.encrypt(v.encode())).decode()
            r = s.put(f"{GH_API}/repos/{repo}/actions/secrets/{k}", json={"encrypted_value": enc, "key_id": pk["key_id"]}, timeout=20)
            if r.status_code in (201, 204):
                count += 1
        steps.append(f"Ustawiono {count} sekretów")

        # 4. uprawnienia zapisu dla workflowów (baza deduplikacji wraca do repo)
        s.put(f"{GH_API}/repos/{repo}/actions/permissions/workflow",
              json={"default_workflow_permissions": "write", "can_approve_pull_request_reviews": False}, timeout=20)
        steps.append("Włączono uprawnienia zapisu dla Actions")

        # 5. pierwszy przebieg
        r = s.post(f"{GH_API}/repos/{repo}/actions/workflows/checker.yml/dispatches", json={"ref": branch}, timeout=20)
        steps.append("Uruchomiono pierwszy przebieg" if r.status_code == 204 else
                     "Workflow pojawi się po chwili – uruchomi się sam wg harmonogramu")
        log(f"GitHub: wdrożono do {repo}")
        return jsonify(ok=True, steps=steps, url=f"https://github.com/{repo}/actions")
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc), steps=steps)


# ---------------------------------------------------------------- start

def main() -> None:
    setup_logging()
    reload_settings()
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n✈️  Panel łowcy działa: {url}\n   (zostaw to okno otwarte; zamknięcie wyłącza panel, ale nie tryb w tle)\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
