"""
Kreator konfiguracji – uruchamiany przez START.command (Mac) lub `python setup_wizard.py`.

Prowadzi za rękę przez wszystkie kroki, bez edytowania plików:
  1. instalacja zależności,
  2. Gmail (hasło aplikacji) + testowy e-mail,
  3. Telegram (logowanie + wybór kanałów z listy),
  4. progi cenowe i lotniska,
  5. uruchomienie: test / w tle na tym komputerze / GitHub Actions (przez `gh`).

Wszystko zapisuje do pliku .env. Można uruchamiać wielokrotnie – istniejące
wartości są proponowane jako domyślne.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / ".env"
PY = sys.executable

# ---------------------------------------------------------------- narzędzia UI

def title(text: str) -> None:
    print("\n" + "=" * 64 + f"\n  {text}\n" + "=" * 64)


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    hint = f" [{'•' * 6 if (secret and default) else default}]" if default else ""
    try:
        if secret and not default:
            import getpass
            value = getpass.getpass(f"{prompt}{hint}: ")
        else:
            value = input(f"{prompt}{hint}: ")
    except (EOFError, KeyboardInterrupt):
        print("\nPrzerwano."); sys.exit(0)
    value = value.strip()
    return value or default


def yes(prompt: str, default: bool = True) -> bool:
    d = "T/n" if default else "t/N"
    v = ask(f"{prompt} ({d})").lower()
    if not v:
        return default
    return v in {"t", "tak", "y", "yes"}


def open_url(url: str) -> None:
    print(f"   → otwieram w przeglądarce: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def run(cmd: list, check: bool = True, capture: bool = False, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, cwd=BASE, **kw)


# ---------------------------------------------------------------- .env

def load_env() -> dict:
    data = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def save_env(data: dict) -> None:
    lines = ["# Wygenerowane przez setup_wizard.py – nie wrzucaj tego pliku na GitHub (jest w .gitignore)"]
    for k, v in data.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.update({k: str(v) for k, v in data.items()})


# ---------------------------------------------------------------- kroki

def step_deps() -> None:
    title("Krok 1/5 – instalacja bibliotek")
    try:
        import telethon, feedparser, bs4, requests, dotenv  # noqa: F401
        print("✓ Biblioteki już zainstalowane")
        return
    except ImportError:
        pass
    print("Instaluję biblioteki (to może potrwać minutę)...")
    run([PY, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=False)
    try:
        import telethon  # noqa: F401
        print("✓ Gotowe")
    except ImportError:
        print("✗ Instalacja nie powiodła się. Uruchom ręcznie: pip install -r requirements.txt")
        sys.exit(1)


def step_email(env: dict) -> None:
    title("Krok 2/5 – e-mail (Gmail)")
    print("Potrzebne jest HASŁO APLIKACJI Gmail (nie zwykłe hasło).")
    print("Jeśli go nie masz: 1) włącz weryfikację dwuetapową, 2) utwórz hasło aplikacji.")
    if yes("Otworzyć stronę tworzenia hasła aplikacji?"):
        open_url("https://myaccount.google.com/apppasswords")
    env["SMTP_USER"] = ask("Twój adres Gmail", env.get("SMTP_USER", ""))
    env["SMTP_PASSWORD"] = ask("Hasło aplikacji (16 znaków)", env.get("SMTP_PASSWORD", ""), secret=True).replace(" ", "")
    env["EMAIL_FROM"] = env["SMTP_USER"]
    env["EMAIL_TO"] = ask("Na jaki adres wysyłać okazje", env.get("EMAIL_TO", env["SMTP_USER"]))
    env.setdefault("SMTP_HOST", "smtp.gmail.com")
    env.setdefault("SMTP_PORT", "587")
    save_env(env)

    print("Wysyłam testowego maila...")
    try:
        _reload_settings()
        from notifiers.email_notifier import EmailNotifier
        from scrapers.base import Deal
        test = Deal("WAW", "BCN", 49, "test", "https://www.google.com/travel/flights",
                    "2026-10-10", "2026-10-14", "To jest testowa wiadomość z kreatora – wszystko działa!",
                    country="ES", reason="test konfiguracji")
        ok = EmailNotifier().send([test])
        print("✓ Wysłano – sprawdź skrzynkę (także SPAM)." if ok else "✗ Nie udało się wysłać – sprawdź hasło aplikacji.")
        if not ok and yes("Spróbować jeszcze raz?"):
            step_email(env)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Błąd: {exc}")


def step_telegram(env: dict) -> None:
    title("Krok 3/5 – Telegram")
    if not yes("Skonfigurować Telegram? (tu okazje pojawiają się najszybciej)"):
        return
    print("Potrzebne są api_id i api_hash z my.telegram.org → 'API development tools'.")
    print("Zaloguj się numerem telefonu, utwórz aplikację (nazwa dowolna, np. 'loty').")
    if yes("Otworzyć my.telegram.org?"):
        open_url("https://my.telegram.org/apps")
    env["TELEGRAM_API_ID"] = ask("api_id (same cyfry)", env.get("TELEGRAM_API_ID", ""))
    env["TELEGRAM_API_HASH"] = ask("api_hash", env.get("TELEGRAM_API_HASH", ""))
    save_env(env)

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.types import Channel
    except ImportError:
        print("✗ Brak biblioteki telethon"); return

    session = env.get("TELEGRAM_SESSION", "")
    if session and not yes("Masz już zapisaną sesję. Zalogować ponownie?", default=False):
        pass
    else:
        print("Logowanie do Telegrama – podaj numer w formacie +48..., potem kod z aplikacji Telegram.")
        try:
            with TelegramClient(StringSession(), int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"]) as client:
                session = client.session.save()
        except Exception as exc:  # noqa: BLE001
            print(f"✗ Logowanie nie powiodło się: {exc}"); return
        env["TELEGRAM_SESSION"] = session
        save_env(env)
        print("✓ Zalogowano")

    # Wybór kanałów z listy tych, do których użytkownik już należy.
    print("\nPobieram listę Twoich kanałów...")
    try:
        with TelegramClient(StringSession(session), int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"]) as client:
            channels = []
            for dialog in client.iter_dialogs():
                ent = dialog.entity
                if isinstance(ent, Channel) and getattr(ent, "username", None):
                    channels.append((ent.username, dialog.name))
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Nie udało się pobrać kanałów: {exc}"); channels = []

    current = set(env.get("TELEGRAM_CHANNELS", "fly4free,lowcymamutow,loterpl").split(","))
    if channels:
        print("Twoje publiczne kanały/grupy (x = już wybrany):")
        for i, (u, name) in enumerate(channels, 1):
            mark = "x" if u in current else " "
            print(f"  [{mark}] {i:2d}. {name}  (t.me/{u})")
        raw = ask("Numery kanałów do śledzenia, po przecinku (Enter = zostaw jak jest)")
        if raw:
            picked = [channels[int(n) - 1][0] for n in re.findall(r"\d+", raw) if 0 < int(n) <= len(channels)]
            env["TELEGRAM_CHANNELS"] = ",".join(picked)
    else:
        print("Nie znaleziono kanałów. Dołącz w Telegramie do kanałów z okazjami (Fly4free, Łowcy Mamutów,")
        print("Pepper, Loter) i uruchom kreator ponownie – albo wpisz nazwy ręcznie.")
        env["TELEGRAM_CHANNELS"] = ask("Nazwy kanałów po przecinku (z t.me/NAZWA)", env.get("TELEGRAM_CHANNELS", "fly4free,lowcymamutow"))
    save_env(env)
    print(f"✓ Śledzone kanały: {env['TELEGRAM_CHANNELS']}")


def step_thresholds(env: dict) -> None:
    title("Krok 4/5 – lotniska i progi cenowe")
    env["HOME_AIRPORTS"] = ask("Lotniska wylotu (kody po przecinku)", env.get("HOME_AIRPORTS", "WAW,WMI,KRK,GDN,KTW,WRO,POZ,BER"))
    env["MAX_PRICE_EUROPE_PLN"] = ask("Europa – powiadom, gdy lot w obie strony tańszy niż (PLN)", env.get("MAX_PRICE_EUROPE_PLN", "60"))
    env["MAX_PRICE_LONGHAUL_PLN"] = ask("Daleki dystans – powiadom, gdy tańszy niż (PLN)", env.get("MAX_PRICE_LONGHAUL_PLN", "1000"))
    env.setdefault("PRICE_DROP_RATIO", "0.5")
    env.setdefault("RSS_FEEDS", "https://www.fly4free.pl/feed/,https://loter.pl/feed/")
    env.setdefault("ENABLE_AIRLINE_PAGES", "0")   # wymaga Playwright – domyślnie wyłączone dla prostoty
    env.setdefault("ENABLE_FACEBOOK", "0")
    env.setdefault("DRY_RUN", "0")
    save_env(env)
    print("✓ Zapisano")


def step_run(env: dict) -> None:
    title("Krok 5/5 – uruchomienie")
    print("Jak ma działać łowca?")
    print("  1. Uruchom teraz jeden raz (test)")
    print("  2. W tle na tym komputerze – Telegram na żywo, powiadomienie w kilka sekund")
    print("     (działa, gdy komputer jest włączony i nie śpi)")
    print("  3. Na GitHub Actions – za darmo w chmurze, co 30 min, komputer może być wyłączony")
    print("  4. Nic teraz")
    choice = ask("Wybierz 1–4", "1")
    if choice == "1":
        run([PY, "main.py"], check=False)
    elif choice == "2":
        install_background()
    elif choice == "3":
        deploy_github(env)


def install_background() -> None:
    if platform.system() != "Darwin":
        print("Tryb w tle przez kreator działa na macOS. Na Linuksie użyj systemd (opis w README).")
        print(f"Możesz też po prostu zostawić otwarte okno z: {PY} main.py --daemon")
        return
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = plist_dir / "pl.errorfare.hunter.plist"
    log = BASE / "data" / "hunter.log"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>pl.errorfare.hunter</string>
  <key>ProgramArguments</key><array>
    <string>{PY}</string><string>{BASE / 'main.py'}</string><string>--daemon</string><string>--interval</string><string>20</string>
  </array>
  <key>WorkingDirectory</key><string>{BASE}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    res = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
    if res.returncode == 0:
        print(f"✓ Łowca działa w tle i uruchomi się sam po każdym starcie komputera.\n   Log: {log}")
        print("   Zatrzymanie: START.command → opcja 'Zatrzymaj tło'.")
    else:
        print(f"✗ launchctl: {res.stderr.strip()}")


def uninstall_background() -> None:
    plist = Path.home() / "Library" / "LaunchAgents" / "pl.errorfare.hunter.plist"
    if plist.exists():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        plist.unlink()
        print("✓ Zatrzymano i usunięto z autostartu")
    else:
        print("Tło nie było włączone")


def deploy_github(env: dict) -> None:
    """Tworzy prywatne repo, wysyła kod, ustawia sekrety i uruchamia workflow – przez GitHub CLI."""
    gh = shutil.which("gh")
    if not gh:
        print("Potrzebny jest program 'gh' (GitHub CLI).")
        if shutil.which("brew") and yes("Zainstalować przez Homebrew (brew install gh)?"):
            run(["brew", "install", "gh"], check=False)
            gh = shutil.which("gh")
        if not gh:
            print("Zainstaluj z https://cli.github.com i uruchom kreator ponownie.")
            open_url("https://cli.github.com")
            return
    if run([gh, "auth", "status"], check=False, capture=True).returncode != 0:
        print("Logowanie do GitHuba – wybierz: GitHub.com → HTTPS → Login with a web browser.")
        if run([gh, "auth", "login", "-p", "https", "-w"], check=False).returncode != 0:
            print("✗ Logowanie nie powiodło się"); return

    if not (BASE / ".git").exists():
        run(["git", "init", "-q"], check=False)
        run(["git", "add", "-A"], check=False)
        run(["git", "-c", "user.name=error-fare", "-c", "user.email=bot@example.com",
             "commit", "-q", "-m", "Initial commit"], check=False)

    name = ask("Nazwa repozytorium", "error-fare-hunter")
    if run([gh, "repo", "view", name], check=False, capture=True).returncode != 0:
        res = run([gh, "repo", "create", name, "--private", "--source", ".", "--push"], check=False)
        if res.returncode != 0:
            print("✗ Nie udało się utworzyć repozytorium"); return
    else:
        run(["git", "push", "-u", "origin", "HEAD"], check=False)
    owner = run([gh, "api", "user", "-q", ".login"], capture=True).stdout.strip()
    repo = f"{owner}/{name}"

    print("Ustawiam sekrety z pliku .env...")
    run([gh, "secret", "set", "-R", repo, "-f", str(ENV_FILE)], check=False)
    print("Włączam uprawnienia zapisu dla workflowów...")
    run([gh, "api", "-X", "PUT", f"repos/{repo}/actions/permissions/workflow",
         "-f", "default_workflow_permissions=write", "-F", "can_approve_pull_request_reviews=false"],
        check=False, capture=True)
    print("Uruchamiam pierwszy przebieg...")
    run([gh, "workflow", "run", "checker.yml", "-R", repo], check=False)
    print(f"✓ Gotowe! Postęp: https://github.com/{repo}/actions")
    print("  Od teraz skaner uruchamia się co 30 minut w chmurze – możesz wyłączyć komputer.")


def _reload_settings() -> None:
    """Przeładuj config.settings po zmianie .env (moduły mogły być już zaimportowane)."""
    import importlib
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=True)
    import config
    importlib.reload(config)
    for mod in ("notifiers.email_notifier", "analyzers.price_analyzer", "scrapers.telegram_scraper"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


# ---------------------------------------------------------------- menu

def full_setup() -> None:
    env = load_env()
    step_deps()
    step_email(env)
    step_telegram(env)
    step_thresholds(env)
    step_run(env)


def ensure_workflow() -> None:
    """Upewnij się, że istnieje .github/workflows/checker.yml (kopiowany z workflow_template.yml)."""
    target = BASE / ".github" / "workflows" / "checker.yml"
    template = BASE / "workflow_template.yml"
    if not target.exists() and template.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template, target)


def main() -> None:
    os.chdir(BASE)
    sys.path.insert(0, str(BASE))
    ensure_workflow()
    configured = ENV_FILE.exists() and load_env().get("SMTP_PASSWORD")
    if not configured:
        print("\n✈️  Witaj w kreatorze łowcy tanich lotów! Przejdziemy przez 5 krótkich kroków.")
        full_setup()
        return
    env = load_env()
    title("✈️  error-fare-hunter – menu")
    print("  1. Uruchom skan teraz (jeden raz)")
    print("  2. Włącz działanie w tle na tym komputerze")
    print("  3. Zatrzymaj tło")
    print("  4. Wyślij / zaktualizuj na GitHub Actions (chmura)")
    print("  5. Zmień e-mail")
    print("  6. Zmień Telegram / kanały")
    print("  7. Zmień lotniska i progi cenowe")
    print("  8. Pokaż ostatnie znalezione okazje")
    print("  9. Zacznij konfigurację od nowa")
    choice = ask("Wybierz", "1")
    actions = {
        "1": lambda: run([PY, "main.py"], check=False),
        "2": install_background,
        "3": uninstall_background,
        "4": lambda: deploy_github(env),
        "5": lambda: step_email(env),
        "6": lambda: step_telegram(env),
        "7": lambda: step_thresholds(env),
        "8": show_recent,
        "9": full_setup,
    }
    actions.get(choice, lambda: None)()


def show_recent() -> None:
    import sqlite3
    db = BASE / "data" / "seen_deals.db"
    if not db.exists():
        print("Jeszcze nic nie znaleziono."); return
    rows = sqlite3.connect(db).execute(
        "SELECT seen_at, route, price_pln, source, url FROM seen_deals ORDER BY seen_at DESC LIMIT 15"
    ).fetchall()
    if not rows:
        print("Jeszcze nic nie znaleziono."); return
    for seen, route, price, source, url in rows:
        print(f"{seen[:16]}  {route:10s} {price:7.0f} PLN  {source:22s} {url}")


if __name__ == "__main__":
    main()
