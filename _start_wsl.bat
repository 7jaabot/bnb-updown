@echo off
REM Polymarket BTC 5mn Paper Trader — WSL2 Launcher

echo ========================================
echo   Polymarket BTC 5mn Paper Trader
echo   (WSL2 mode)
echo ========================================
echo.

wsl bash /home/joris/.openclaw/workspace/repos/polymarket-btc/_start_wsl.sh %*

pause
