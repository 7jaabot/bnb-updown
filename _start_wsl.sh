#!/bin/bash
# PancakeSwap BNB/USD 5mn Paper Trader — WSL2 Launcher

REPO="/home/joris/.openclaw/workspace/repos/prdt-btc"

echo "========================================"
echo "  PancakeSwap BNB/USD 5mn Paper Trader"
echo "  (BSC live data)"
echo "========================================"
echo ""

cd "$REPO" || { echo "ERROR: repo not found at $REPO"; exit 1; }

# Create venv if needed
if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install deps
source venv/bin/activate
venv/bin/python3 -m pip install -r requirements.txt -q

# Start the bot (always fresh — resets paper trading history)
echo "Starting bot (fresh start, live BSC data)..."
echo ""
python src/main.py --fresh "$@"
