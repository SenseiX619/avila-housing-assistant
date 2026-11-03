@echo off
REM ============================================================
REM  Avila Housing Assistant - run the offline tests (double-click me)
REM  No internet or Groq key needed. Your real tickets are not touched.
REM ============================================================
cd /d "%~dp0"
if not exist .venv (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
set FAKE_LLM=1
python -m unittest discover -s tests -v
pause
