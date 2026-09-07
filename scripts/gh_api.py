"""
Mini-klient GitHub REST API używany wewnątrz GitHub Actions.

Potrzebny, bo standardowy GITHUB_TOKEN nie może zapisywać sekretów –
używamy tokenu użytkownika (secret GH_PAT), ustawionego przez instalator.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token or os.environ["GH_PAT"]
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]  # "owner/name"
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    # --- sekrety --------------------------------------------------------
    def set_secret(self, name: str, value: str) -> None:
        from nacl import encoding, public
        pk = self.s.get(f"{API}/repos/{self.repo}/actions/secrets/public-key", timeout=20).json()
        box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
        r = self.s.put(f"{API}/repos/{self.repo}/actions/secrets/{name}",
                       json={"encrypted_value": enc, "key_id": pk["key_id"]}, timeout=20)
        r.raise_for_status()

    def delete_secret(self, name: str) -> None:
        self.s.delete(f"{API}/repos/{self.repo}/actions/secrets/{name}", timeout=20)

    # --- zmienne --------------------------------------------------------
    def set_variable(self, name: str, value: str) -> None:
        r = self.s.patch(f"{API}/repos/{self.repo}/actions/variables/{name}",
                         json={"name": name, "value": value}, timeout=20)
        if r.status_code == 404:
            self.s.post(f"{API}/repos/{self.repo}/actions/variables",
                        json={"name": name, "value": value}, timeout=20).raise_for_status()


def write_status(step: str, ok: bool, message: str, **extra) -> None:
    """Zapisz data/status.json – strona na telefonie odczytuje go po każdym przebiegu."""
    path = Path(__file__).resolve().parents[1] / "data" / "status.json"
    path.parent.mkdir(exist_ok=True)
    payload = {"step": step, "ok": ok, "message": message,
               "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
