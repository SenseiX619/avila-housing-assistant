@echo off
REM ============================================================
REM  Avila Housing Assistant - put the app online for phones (double-click me)
REM  Gives this laptop's app a secure https link + QR code. Keep this window open.
REM ============================================================
cd /d "%~dp0"
if not exist .venv (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python check_setup.py || (pause & exit /b 1)
python online.py
pause
