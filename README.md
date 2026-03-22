# bnb-updown

BTC UP/DOWN 5-minute prediction bot for **PRDT Finance** (fork of polymarket-btc).

## Key differences vs polymarket-btc

| | polymarket-btc | bnb-updown |
|---|---|---|
| Market | Polymarket (REST API) | PRDT Finance (on-chain, Polygon) |
| Price signal | YES price from order book | Bull/bear pool ratio via smart contract |
| Geo-block | 🚫 Blocked in Switzerland | ✅ No restrictions (DeFi) |
| KYC | Required for deposits | ❌ Wallet only |
| Collateral | USDC | USDC/USDT |

## Strategy

Same momentum logic as polymarket-btc:
1. Monitor BTC price via Binance WebSocket
2. In the last 60s of each 5-min window, estimate P(BTC goes up) via GBM Monte Carlo
3. Convert PRDT pool ratio → break-even probability (`bull_ratio / (1 - fee)`)
4. If edge > threshold → enter paper trade

## Setup

```bash
# 1. Install deps (web3.py required for live data)
pip install -r requirements.txt

# 2. Run (fresh start)
python src/main.py --fresh

# 3. Dry-run (test Polygon connectivity)
python src/main.py --dry-run
```

## Live trading (when ready)

- Connect MetaMask to Polygon
- Deposit USDC to PRDT Finance at <https://prdt.finance/classic>
- Adapt bot to sign transactions (future work)
