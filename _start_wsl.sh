#!/bin/bash
# PancakeSwap BNB/USD 5mn Trading Bot — WSL2 Launcher

REPO="/home/joris/projects/bnb-updown"

echo "========================================"
echo "  PancakeSwap BNB/USD 5mn Trading Bot"
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

# Launch bot with interactive menu (no --fresh, no --live → shows menu)
python src/main.py "$@"
