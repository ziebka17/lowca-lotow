"""
Wysyłka powiadomień e-mail.

Kolejność prób:
  1. SendGrid API  – jeśli ustawiono SENDGRID_API_KEY (proste zapytanie HTTP,
                     nie wymaga biblioteki sendgrid),
  2. SMTP (smtplib) – Gmail z hasłem aplikacji, Outlook, Onet, WP itd.

Jeden e-mail może zawierać kilka okazji (batch z jednego uruchomienia) –
mniej spamu w skrzynce, a nadal natychmiastowo.
"""
from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage
from typing import List

import requests

from config import settings
from scrapers.base import Deal
from utils import airports
from utils.logger import get_logger

log = get_logger(__name__)


def _city(code: str) -> str:
    """„WAW” -> „Warszawa (WAW)”, nieznany kod -> sam kod."""
    name = airports.IATA_NAME.get(code)
    return f"{name} ({code})" if name else code


def _dates(deal: Deal) -> str:
    if deal.date_from and deal.date_to:
        return f"{deal.date_from} → {deal.date_to}"
    if deal.date_from:
        return str(deal.date_from)
    return "sprawdź w źródle"


def build_subject(deals: List[Deal]) -> str:
    if len(deals) == 1:
        d = deals[0]
        return f"✈️ OKAZJA: {_city(d.origin)} → {_city(d.destination)} za {d.price_pln:.0f} PLN"
    cheapest = min(deals, key=lambda d: d.price_pln)
    return f"✈️ {len(deals)} okazji lotniczych (od {cheapest.price_pln:.0f} PLN)"


def build_text(deals: List[Deal]) -> str:
    """Wersja tekstowa (dla klientów bez HTML)."""
    lines = ["Znalezione okazje lotnicze:\n"]
    for d in deals:
        lines += [
            f"{_city(d.origin)} → {_city(d.destination)}",
            f"  Daty:    {_dates(d)}",
            f"  Cena:    {d.price_pln:.0f} PLN" + (f" (mediana {d.baseline_pln:.0f} PLN)" if d.baseline_pln else ""),
            f"  Dlaczego: {d.reason}",
            f"  Źródło:  {d.source}",
            f"  Opis:    {d.description[:200]}",
            f"  Link:    {d.url}",
            "",
        ]
    lines.append("— error-fare-hunter")
    return "\n".join(lines)


def build_html(deals: List[Deal]) -> str:
    cards = []
    for d in deals:
        baseline = (
            f'<span style="color:#888;text-decoration:line-through;margin-left:8px">{d.baseline_pln:.0f} PLN</span>'
            if d.baseline_pln
            else ""
        )
        cards.append(
            f"""
            <div style="border:1px solid #e3e3e3;border-radius:10px;padding:16px;margin:0 0 14px 0;font-family:Arial,sans-serif">
              <div style="font-size:18px;font-weight:bold;margin-bottom:6px">
                {html.escape(_city(d.origin))} → {html.escape(_city(d.destination))}
              </div>
              <div style="font-size:26px;color:#0a7d2c;font-weight:bold;margin-bottom:8px">
                {d.price_pln:.0f} PLN {baseline}
              </div>
              <table style="font-size:14px;color:#333;border-collapse:collapse">
                <tr><td style="padding:2px 10px 2px 0;color:#777">Daty</td><td>{html.escape(_dates(d))}</td></tr>
                <tr><td style="padding:2px 10px 2px 0;color:#777">Dlaczego</td><td>{html.escape(d.reason)}</td></tr>
                <tr><td style="padding:2px 10px 2px 0;color:#777">Źródło</td><td>{html.escape(d.source)}{(' · ' + html.escape(d.airline)) if d.airline else ''}</td></tr>
              </table>
              <p style="font-size:13px;color:#555;margin:10px 0">{html.escape(d.description[:300])}</p>
              <a href="{html.escape(d.url)}" style="display:inline-block;background:#1a73e8;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold">
                Rezerwuj / zobacz ofertę →
              </a>
            </div>
            """
        )
    return f"""
    <html><body style="background:#f6f6f6;padding:20px">
      <div style="max-width:620px;margin:auto">
        <h2 style="font-family:Arial,sans-serif;color:#222">✈️ Wykryte okazje lotnicze</h2>
        {''.join(cards)}
        <p style="font-family:Arial,sans-serif;font-size:12px;color:#999">
          Error fares znikają w ciągu godzin – rezerwuj szybko i sprawdź warunki. Wysłano automatycznie przez error-fare-hunter.
        </p>
      </div>
    </body></html>
    """


class EmailNotifier:
    def enabled(self) -> bool:
        if not settings.email_to:
            log.warning("E-mail: brak EMAIL_TO – powiadomienia wyłączone")
            return False
        return bool(settings.sendgrid_key or (settings.smtp_user and settings.smtp_password))

    # ------------------------------------------------------------------ #
    def send(self, deals: List[Deal]) -> bool:
        """Wyślij jedno powiadomienie z listą okazji. Zwraca True przy sukcesie."""
        if not deals:
            return False
        subject = build_subject(deals)
        text = build_text(deals)
        body_html = build_html(deals)

        if settings.dry_run:
            log.info("[DRY RUN] Wysłałbym e-mail: %s\n%s", subject, text)
            return True

        if settings.sendgrid_key:
            if self._send_sendgrid(subject, text, body_html):
                return True
            log.warning("SendGrid nie zadziałał – próbuję SMTP")
        return self._send_smtp(subject, text, body_html)

    # ------------------------------------------------------------------ #
    def _send_sendgrid(self, subject: str, text: str, body_html: str) -> bool:
        try:
            resp = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {settings.sendgrid_key}"},
                json={
                    "personalizations": [{"to": [{"email": e} for e in settings.email_to]}],
                    "from": {"email": settings.email_from},
                    "subject": subject,
                    "content": [
                        {"type": "text/plain", "value": text},
                        {"type": "text/html", "value": body_html},
                    ],
                },
                timeout=20,
            )
            if resp.status_code in (200, 202):
                log.info("E-mail (SendGrid) wysłany: %s", subject)
                return True
            log.error("SendGrid odpowiedział %s: %s", resp.status_code, resp.text[:300])
        except requests.RequestException as exc:
            log.error("SendGrid: błąd połączenia: %s", exc)
        return False

    def _send_smtp(self, subject: str, text: str, body_html: str) -> bool:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.email_from
        msg["To"] = ", ".join(settings.email_to)
        msg.set_content(text)
        msg.add_alternative(body_html, subtype="html")
        try:
            context = ssl.create_default_context()
            if settings.smtp_port == 465:
                with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30) as server:
                    server.login(settings.smtp_user, settings.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.login(settings.smtp_user, settings.smtp_password)
                    server.send_message(msg)
            log.info("E-mail (SMTP) wysłany: %s", subject)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("SMTP: nie udało się wysłać e-maila: %s", exc)
            return False
