"""
Buduje INSTALATOR.html = docs/index.html + wszystkie pliki projektu zakodowane w base64.

Użytkownik otwiera INSTALATOR.html w przeglądarce, wkleja token GitHub, a strona
sama tworzy repozytorium, wysyła pliki, ustawia sekrety i włącza GitHub Pages.
Uruchom po każdej zmianie kodu:  python build_installer.py
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
SKIP_FILES = {"INSTALATOR.html", ".env", ".DS_Store", "hunter.log", "daemon.pid", "seen_deals.db",
              "last_run.json", "status.json", "deals.json", "channels.json"}


def bundle() -> dict:
    files = {}
    for p in sorted(BASE.rglob("*")):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.parts) or p.name in SKIP_FILES:
            continue
        if p.suffix in {".db", ".log", ".pid"}:
            continue
        files[p.relative_to(BASE).as_posix()] = base64.b64encode(p.read_bytes()).decode()
    return files


def main() -> None:
    html = (BASE / "docs" / "index.html").read_text(encoding="utf-8")
    payload = json.dumps(bundle(), ensure_ascii=True).replace("</", "<\\/")
    tag = f'<script id="bundle" type="application/json">{payload}</script>\n</body>'
    out = html.replace("</body>", tag, 1)
    (BASE / "INSTALATOR.html").write_text(out, encoding="utf-8")
    print(f"INSTALATOR.html: {len(out)//1024} KB, {len(bundle())} plików")


if __name__ == "__main__":
    main()
