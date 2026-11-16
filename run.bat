@echo off
chcp 65001 >nul
REM ตัวช่วยรันบน Windows: run.bat check 1234567890123
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt pywebview
)
.venv\Scripts\python -m nhso_right_check %*
