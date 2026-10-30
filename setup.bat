@echo off
REM ============================================================
REM  Avila Housing Assistant - one-time setup (double-click me)
REM ============================================================
cd /d "%~dp0"
echo.
echo === 1/4  Checking Python ===
where python >nul 2>nul || (echo Python was not found. Install it from python.org and tick "Add to PATH". & pause & exit /b 1)
python --version

echo.
echo === 2/4  Creating a private Python environment (.venv) ===
if not exist .venv python -m venv .venv || goto :error
call .venv\Scripts\activate.bat

echo.
echo === 3/4  Installing libraries (takes 1-3 minutes) ===
python -m pip install --upgrade pip -q
pip install -r requirements.txt || goto :error

echo.
echo === 4/4  Downloading and reading the Avila Housing Handbook ===
if not exist .env copy .env.example .env >nul
python ingest.py

echo.
echo ============================================================
echo  Setup finished!
echo  NEXT: open the file named .env in VS Code, paste your Groq key,
echo        save it, then double-click run.bat
echo ============================================================
pause
exit /b 0

:error
echo.
echo Something went wrong. Copy the red error text above and send it to Claude.
pause
exit /b 1
