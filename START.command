#!/bin/bash
# macOS: kliknij dwa razy ten plik. Otworzy panel w przeglądarce (http://127.0.0.1:8765).
cd "$(dirname "$0")"

# 1. Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "Brak Pythona. macOS zaproponuje instalację narzędzi – kliknij 'Zainstaluj', poczekaj i uruchom ten plik ponownie."
  xcode-select --install 2>/dev/null
  read -p "Naciśnij Enter, aby zamknąć..."
  exit 1
fi

# 2. Wirtualne środowisko (izolowane biblioteki, nic nie psuje w systemie)
if [ ! -d ".venv" ]; then
  echo "Przygotowuję środowisko (pierwszy raz, ~1 min)..."
  python3 -m venv .venv || { echo "Nie udało się utworzyć środowiska"; read -p "Enter..."; exit 1; }
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip >/dev/null 2>&1

# 3. Biblioteki + panel w przeglądarce
python -c "import flask, telethon, feedparser, nacl" 2>/dev/null || { echo "Instaluję biblioteki (pierwszy raz, ~1-2 min)..."; python -m pip install -q -r requirements.txt; }
python app.py

echo
read -p "Naciśnij Enter, aby zamknąć okno..."
