# ✈️ error-fare-hunter

Automatyczny łowca **error fares** i okazji lotniczych z polskich lotnisk
(WAW, WMI, KRK, GDN, KTW, WRO, POZ, RZE, LUZ, SZZ, BZG, LCJ oraz BER przy granicy).
Skanuje API porównywarek, kanały Telegram, RSS portali z okazjami, strony promocyjne
linii lotniczych i (opcjonalnie) publiczne strony na Facebooku, a każdą nową okazję
wysyła na Twój e-mail z linkiem do rezerwacji.

Działa **za darmo** na GitHub Actions (cron co 30 minut) albo w trybie ciągłym na
dowolnym VPS / Raspberry Pi (Telegram nasłuchiwany w czasie rzeczywistym).

## Najprostszy start – telefon, bez komputera (zalecane)

1. Na komputerze otwórz **`INSTALATOR.html`** (dwuklik). Strona poprosi o token GitHub
   (link tworzy go z właściwymi uprawnieniami) i po kliknięciu „Zainstaluj” sama:
   utworzy repozytorium na Twoim koncie, wgra kod, zapisze sekrety, włączy skaner
   w chmurze (GitHub Actions, co 15 min, za darmo) i opublikuje stronę sterującą.
2. Na telefonie otwórz `https://TWOJ-LOGIN.github.io/lowca-lotow/`, wklej ten sam token
   i dodaj stronę do ekranu głównego. Od tej chwili wszystko robisz z telefonu:
   Gmail (+ testowy mail), logowanie do Telegrama (kod z aplikacji), wybór kanałów,
   progi cenowe, „Skanuj teraz”, lista znalezionych okazji.

Komputer może być wyłączony – skaner i strona działają w chmurze GitHuba.
Logowanie do Telegrama z telefonu odbywa się przez workflow `telegram-login.yml`
(krok 1: numer → kod, krok 2: kod → sesja zapisana jako sekret).

## Alternatywa: panel lokalny na komputerze

`START.command` (Mac) / `START.bat` (Windows) uruchamia panel pod http://127.0.0.1:8765
z tymi samymi funkcjami plus tryb „w tle” (Telegram na żywo, mail w kilka sekund od posta,
wymaga włączonego komputera). `python setup_wizard.py` – kreator tekstowy.

Reszta tego README to opis „pod maską” i konfiguracja ręczna dla chętnych.

```
error-fare-hunter/
├── INSTALATOR.html              # jednorazowa instalacja w chmurze (buduje build_installer.py)
├── docs/index.html              # strona sterująca na telefon (GitHub Pages)
├── scripts/                     # telegram_login.py, export_state.py, gh_api.py – używane w Actions
├── START.command / START.bat    # dwuklik → panel lokalny w przeglądarce
├── app.py + templates/index.html # panel WWW (Flask): ustawienia, Telegram, skan, tło, GitHub
├── setup_wizard.py              # alternatywny kreator tekstowy
├── main.py                      # punkt wejścia: tryb jednorazowy i --daemon
├── config.py                    # ustawienia z .env / GitHub Secrets
├── scrapers/
│   ├── base.py                  # model Deal + parsery cen/dat/miast z tekstu
│   ├── api_scraper.py           # Kiwi Tequila, SerpAPI (Google Flights), Duffel
│   ├── telegram_scraper.py      # Telethon: kanały Fly4free, Łowcy Mamutów, Pepper, Loter
│   └── web_scraper.py           # RSS, strony promocyjne Ryanair/Wizz/LOT, Facebook (Playwright)
├── analyzers/price_analyzer.py  # progi cenowe + odchylenie od mediany + filtr geo
├── notifiers/email_notifier.py  # SMTP (smtplib) lub SendGrid
├── utils/
│   ├── deduplicator.py          # SQLite: widziane oferty, historia cen, stan
│   ├── airports.py              # słownik lotnisk i kierunków (z polską odmianą)
│   └── logger.py
├── tests/test_core.py           # testy logiki (pytest)
├── data/seen_deals.db           # baza – tworzona automatycznie, commitowana przez Actions
├── .github/workflows/checker.yml        # skaner co 15 min
├── .github/workflows/telegram-login.yml # logowanie Telegram z telefonu
├── .env.example
└── requirements.txt
```

## Jak to działa

1. **Scrapery** zwracają listę ofert (`Deal`: skąd, dokąd, daty, cena w PLN, źródło, link).
2. **Analizator** odrzuca loty spoza polskich lotnisk, a resztę uznaje za okazję, gdy:
   * cena po Europie ≤ `MAX_PRICE_EUROPE_PLN` (domyślnie 60 zł) **lub**
   * cena na daleki dystans ≤ `MAX_PRICE_LONGHAUL_PLN` (domyślnie 1000 zł) **lub**
   * cena ≤ `PRICE_DROP_RATIO` × mediana historyczna trasy (domyślnie −50 %; mediana
     buduje się sama z wyników API przy kolejnych uruchomieniach).
3. **Deduplikator** (SQLite) pilnuje, by ten sam post / ta sama oferta nie przyszła dwa razy.
4. **Notifier** wysyła jeden e-mail ze wszystkimi nowymi okazjami z danego przebiegu.
   Oferta jest oznaczana jako „widziana” dopiero po udanej wysyłce.

---

## Szybki start (lokalnie, 5 minut)

```bash
git clone https://github.com/TWOJ-LOGIN/error-fare-hunter.git
cd error-fare-hunter
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium                 # opcjonalnie (strony linii / Facebook)
cp .env.example .env                                  # i uzupełnij – patrz niżej
python main.py --dry-run                              # test bez wysyłania maili
python main.py                                        # prawdziwe uruchomienie
pytest                                                # testy logiki
```

Wymagany Python 3.10+.

---

## Konfiguracja krok po kroku

Wszystko ustawiasz w pliku `.env` (lokalnie) lub jako **Secrets/Variables** w GitHub.
Puste pole = źródło jest pomijane, więc możesz zacząć od samych RSS + e-maila i
dokładać kolejne źródła.

### 1. E-mail (obowiązkowe)

**Opcja A – Gmail przez SMTP (najprościej):**
1. Włącz weryfikację dwuetapową na koncie Google.
2. Wejdź na https://myaccount.google.com/apppasswords → utwórz „hasło aplikacji”.
3. W `.env`:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=twoj.mail@gmail.com
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx
   EMAIL_FROM=twoj.mail@gmail.com
   EMAIL_TO=twoj.mail@gmail.com
   ```
   Inne skrzynki: Outlook `smtp.office365.com:587`, Onet `smtp.poczta.onet.pl:465`,
   WP `smtp.wp.pl:465` (port 465 = SSL, 587 = STARTTLS – skrypt wykrywa sam).

**Opcja B – SendGrid (100 maili/dzień za darmo):**
1. Załóż konto na https://sendgrid.com → *Settings → API Keys → Create API Key* (Full Access).
2. *Settings → Sender Authentication* → zweryfikuj adres nadawcy (Single Sender).
3. W `.env`: `SENDGRID_API_KEY=SG.xxxxx`, `EMAIL_FROM=` zweryfikowany adres, `EMAIL_TO=` Twój adres.

### 2. Telegram (bardzo polecane – tu okazje pojawiają się najszybciej)

1. Wejdź na https://my.telegram.org → *API development tools* → utwórz aplikację
   (dowolna nazwa). Skopiuj **App api_id** i **App api_hash**.
2. W `.env` wpisz `TELEGRAM_API_ID=...` i `TELEGRAM_API_HASH=...`.
3. Jednorazowo, **lokalnie**, wygeneruj sesję:
   ```bash
   python -m scrapers.telegram_scraper --login
   ```
   Podaj numer telefonu i kod z Telegrama. Skrypt wypisze długi ciąg znaków –
   wklej go jako `TELEGRAM_SESSION`. Dzięki temu w GitHub Actions nie trzeba już
   nic potwierdzać.
4. `TELEGRAM_CHANNELS` – publiczne nazwy kanałów bez `@`, rozdzielone przecinkami.
   Domyślnie: `fly4free,lowcymamutow,loterpl`. Sprawdź w Telegramie
   aktualne nazwy (link `t.me/NAZWA`) – kanały czasem zmieniają adresy. Do grup
   prywatnych musisz najpierw dołączyć z tego konta.

> Konto Telegram używane przez bota traktuj jak zwykłe konto – nie spamuj, nie
> dołączaj masowo do grup, bo Telegram może je czasowo ograniczyć.

### 3. API porównywarek (opcjonalnie, wystarczy jedno)

| Źródło | Koszt | Jak zdobyć klucz | Uwagi |
|---|---|---|---|
| **SerpAPI – Google Flights** | 100 zapytań/mies. za darmo | https://serpapi.com → rejestracja → *Your API Key* → `SERPAPI_KEY` | Najłatwiej dostępne. Skrypt sprawdza tylko 8 tras z `API_DESTINATIONS` na przebieg – przy cronie co 30 min zużyjesz limit w ~kilka godzin, więc **dla SerpAPI ustaw osobny, rzadszy harmonogram** (np. raz dziennie) albo zmniejsz listę kierunków. |
| **Kiwi Tequila** | darmowe dla partnerów | https://tequila.kiwi.com → wniosek o dostęp → `KIWI_API_KEY` | Najlepsze do error fares (`fly_to=anywhere`), ale Kiwi od 2024 r. wydaje klucze bardzo wybiórczo. Jeśli nie dostaniesz – zostaw puste. |
| **Duffel** | tryb testowy za darmo | https://app.duffel.com → *Developers → Access tokens → Test* → `DUFFEL_ACCESS_TOKEN` | W trybie testowym zwraca oferty testowej linii, więc służy do sprawdzenia pipeline'u; ceny live wymagają umowy. |

`API_DESTINATIONS` – lista kodów IATA sprawdzanych przez SerpAPI/Duffel
(domyślnie: BCN, LIS, ROM, LON, PAR, MAD, ATH, NYC, BKK, TYO, DXB).

### 4. RSS, strony linii, Facebook

* `RSS_FEEDS` – domyślnie Fly4free, Loter i Pepper (kategoria podróże). Możesz
  dopisać dowolny feed, np. `https://www.wakacyjnipiraci.pl/feed/`.
* `ENABLE_AIRLINE_PAGES=1` – skanuje strony promocji Ryanair / Wizz Air / LOT.
  Strony te są renderowane w JS, więc bez Playwrighta zwykle nic nie znajdą;
  workflow instaluje Chromium automatycznie.
* `ENABLE_FACEBOOK=1` + `FACEBOOK_PAGES=fly4free,lowcymamutow` – Playwright
  otwiera mobilną wersję publicznej strony i czyta widoczne posty. Facebook
  często pokazuje ekran logowania i blokuje boty – to źródło jest „best effort”
  i domyślnie wyłączone. Grupy prywatne nie są dostępne.

### 5. Progi i lotniska

```
HOME_AIRPORTS=WAW,WMI,KRK,GDN,KTW,WRO,POZ,BER   # usuń BER, jeśli nie chcesz Berlina
MAX_PRICE_EUROPE_PLN=60
MAX_PRICE_LONGHAUL_PLN=1000
PRICE_DROP_RATIO=0.5                             # 0.4 = wymagaj spadku o 60 %
```

---

## Uruchomienie za darmo na GitHub Actions

1. Utwórz **prywatne** repozytorium na GitHub i wgraj pliki (przez stronę
   *Add file → Upload files* albo `git push`). Prywatne repo ma 2000 minut Actions
   miesięcznie – jeden przebieg trwa 1–3 min, więc co 30 minut mieści się w limicie
   (publiczne repo ma limit nieograniczony, ale nie trzymaj tam bazy z linkami, jeśli
   nie chcesz).
2. *Settings → Secrets and variables → Actions → Secrets → New repository secret* –
   dodaj po kolei sekrety z `.env.example`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
   `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
   `TELEGRAM_SESSION`, ewentualnie `SERPAPI_KEY`, `KIWI_API_KEY`, `DUFFEL_ACCESS_TOKEN`,
   `SENDGRID_API_KEY`.
3. W zakładce **Variables** (nie Secrets) możesz nadpisać ustawienia nietajne:
   `HOME_AIRPORTS`, `MAX_PRICE_EUROPE_PLN`, `TELEGRAM_CHANNELS`, `RSS_FEEDS`,
   `ENABLE_FACEBOOK`, `DRY_RUN` (ustaw `1` na pierwszy test).
4. *Settings → Actions → General → Workflow permissions* → zaznacz
   **Read and write permissions** (workflow zapisuje bazę deduplikacji do repo).
5. Zakładka **Actions → Error fare checker → Run workflow** – uruchom ręcznie i
   sprawdź logi. Potem cron uruchamia go sam co 30 minut.

Częstotliwość zmienisz w `.github/workflows/checker.yml` (`cron: "*/30 * * * *"`).
GitHub potrafi opóźnić zadania cron o kilka–kilkanaście minut w godzinach szczytu;
jeśli zależy Ci na sekundach, użyj trybu ciągłego.

## Tryb ciągły (VPS, Raspberry Pi, stary laptop)

```bash
python main.py --daemon --interval 20
```
Telegram jest nasłuchiwany **w czasie rzeczywistym** (powiadomienie w kilka sekund
od posta), a API/RSS/WWW skanowane co 20 minut. Najprościej zostawić to w `tmux`
lub jako usługę `systemd`:

```ini
# /etc/systemd/system/error-fare-hunter.service
[Unit]
Description=error-fare-hunter
After=network-online.target
[Service]
WorkingDirectory=/home/pi/error-fare-hunter
ExecStart=/home/pi/error-fare-hunter/.venv/bin/python main.py --daemon
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
```

Darmowe VPS-y do tego celu: Oracle Cloud Always Free, Google Cloud e2-micro,
fly.io (mała maszyna).

---

## Przydatne polecenia

| Polecenie | Co robi |
|---|---|
| `python main.py --dry-run` | pełny przebieg, maile tylko w logu |
| `python main.py --only telegram` | tylko Telegram (podobnie `api`, `web`) |
| `python -m scrapers.telegram_scraper --login` | generuje `TELEGRAM_SESSION` |
| `LOG_LEVEL=DEBUG python main.py --dry-run` | pokazuje, dlaczego oferty są odrzucane |
| `sqlite3 data/seen_deals.db "select * from seen_deals order by seen_at desc limit 10"` | ostatnio wysłane okazje |

## Najczęstsze problemy

* **Brak maili, w logu „Brak nowych okazji”** – to normalne: prawdziwe error fares
  zdarzają się kilka razy w miesiącu. Uruchom z `LOG_LEVEL=DEBUG`, żeby zobaczyć,
  co zostało odrzucone i ewentualnie podnieś progi.
* **SMTP: `Username and Password not accepted`** – Gmail wymaga hasła aplikacji,
  nie zwykłego hasła; usuń spacje lub zostaw – oba działają.
* **Telegram: `Could not find the input entity`** – kanał zmienił nazwę albo jest
  prywatny; sprawdź `t.me/NAZWA` i dołącz z konta bota.
* **Telegram w Actions prosi o kod** – `TELEGRAM_SESSION` jest pusty lub
  wygenerowany innym `api_id`; wygeneruj ponownie.
* **Playwright: `Executable doesn't exist`** – `python -m playwright install chromium`.
* **Ten sam post przyszedł dwa razy** – w Actions baza nie została zapisana:
  sprawdź uprawnienia „Read and write” dla workflowów (krok 4 wyżej).

## Rozszerzanie

Nowe źródło = nowa klasa dziedzicząca po `scrapers.base.BaseScraper` z metodą
`fetch()` zwracającą listę `Deal`, dopisana do `build_scrapers()` w `main.py`.
Parsery `extract_price_pln`, `extract_dates`, `airports.find_origin` /
`find_destination` są gotowe do użycia w każdym scraperze tekstowym.

## Zastrzeżenia

Projekt korzysta z publicznych treści i oficjalnych API. Scrapowanie stron
(zwłaszcza Facebooka) może naruszać ich regulaminy i jest domyślnie wyłączone –
używasz na własną odpowiedzialność. Error fares bywają anulowane przez linie
lotnicze; nie rezerwuj hoteli, dopóki bilet nie zostanie potwierdzony.

Licencja: MIT.
