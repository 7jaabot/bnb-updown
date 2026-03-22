@echo off
REM PancakeSwap BNB/USD 5mn Paper Trader — WSL2 Launcher

echo ========================================
echo   PancakeSwap BNB/USD 5mn Paper Trader
echo   (BSC live data)
echo ========================================
echo.

wsl bash /home/joris/.openclaw/workspace/repos/prdt-btc/_start_wsl.sh %*

pause
