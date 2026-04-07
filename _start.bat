@echo off
REM PancakeSwap BNB/USD 5mn Trading Bot — Windows Launcher

set REPO=%~dp0

echo ========================================
echo   PancakeSwap BNB/USD 5mn Trading Bot
echo ========================================
echo.

cd /d "%REPO%"

REM Create venv if needed
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Install/update deps
venv\Scripts\python.exe -m pip install -r requirements.txt -q

REM Launch bot
venv\Scripts\python.exe src\main.py %*

pause
