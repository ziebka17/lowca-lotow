"""
Po każdym przebiegu skanera: zapisz data/deals.json (ostatnie okazje) i data/status.json,
które czyta strona na telefonie. Uruchamiany w checker.yml po main.py.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gh_api import write_status  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"


def main() -> None:
    DATA.mkdir(exist_ok=True)
    deals = []
    db = DATA / "seen_deals.db"
    if db.exists():
        try:
            rows = sqlite3.connect(db).execute(
                "SELECT seen_at, route, price_pln, source, url FROM seen_deals ORDER BY seen_at DESC LIMIT 60"
            ).fetchall()
            deals = [{"seen_at": r[0][:16].replace("T", " "), "route": r[1], "price": r[2], "source": r[3], "url": r[4]}
                     for r in rows]
        except sqlite3.Error:
            pass
    (DATA / "deals.json").write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")

    last = {}
    lr = DATA / "last_run.json"
    if lr.exists():
        try:
            last = json.loads(lr.read_text(encoding="utf-8"))
        except ValueError:
            pass
    mode = last.get("mode", "scan")
    if mode == "test_email":
        ok = bool(last.get("ok"))
        write_status("test_email", ok, "Testowy e-mail wysłany – sprawdź skrzynkę (także SPAM)." if ok
                     else f"Nie udało się wysłać testowego maila: {last.get('error', 'sprawdź hasło aplikacji')}")
    else:
        sent = int(last.get("sent", 0))
        ok = last.get("ok", True)
        msg = (f"Skan zakończony – wysłano {sent} okazji." if sent else "Skan zakończony – brak nowych okazji.") if ok \
            else f"Skan zakończony błędem: {last.get('error', '')}"
        write_status("scan", bool(ok), msg, sent=sent, sources=last.get("sources", []))


if __name__ == "__main__":
    main()
