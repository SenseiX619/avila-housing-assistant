@echo off
REM ============================================================
REM  Avila Housing Assistant - start the app (double-click me)
REM ============================================================
cd /d "%~dp0"
if not exist .venv (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python check_setup.py || (pause & exit /b 1)
streamlit run app.py
pause
