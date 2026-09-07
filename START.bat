@echo off
REM Windows: kliknij dwa razy. Wymaga Pythona z https://python.org (zaznacz "Add to PATH").
cd /d "%~dp0"
where python >nul 2>nul || (echo Brak Pythona - zainstaluj z https://python.org i zaznacz "Add python to PATH" & pause & exit /b 1)
if not exist .venv ( echo Przygotowuje srodowisko... & python -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip >nul 2>nul
python -c "import flask, telethon, feedparser, nacl" 2>nul || python -m pip install -q -r requirements.txt
python app.py
pause
