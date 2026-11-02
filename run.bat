@echo off
REM ============================================================
REM  Avila Housing Assistant - start the app (double-click me)
REM ============================================================
cd /d "%~dp0"
if not exist .venv (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python check_setup.py || (pause & exit /b 1)
REM Skip Streamlit's one-time "enter your email" prompt
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (mkdir "%USERPROFILE%\.streamlit" 2>nul & (echo [general]& echo email = "")> "%USERPROFILE%\.streamlit\credentials.toml")
streamlit run app.py
pause
