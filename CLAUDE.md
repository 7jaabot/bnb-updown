# bnb-updown

## Statut : ACTIF (paper trading, 17 stratégies en parallèle)

## Description
Bot de trading sur PancakeSwap Prediction V2 (BNB/USD, BSC) — 17 stratégies + mode combiné, paper et live.

## Repo
- Local : `C:\Users\Joris\projects\bnb-updown`
- GitHub : `7jaabot/bnb-updown` (branche `master`)

## Lancement
Double-clic sur `_start.bat` → Windows natif → menu mode (1=live, 2=paper) → menu stratégie :
- **[0]** → lance les 17 stratégies en parallèle (ParallelBot, un seul process)
- **[1-17]** → stratégie individuelle
- **"5,11,7"** → combined strategy (consensus)

## Architecture
- `src/main.py` — orchestrateur principal + ParallelBot, boucle sniper 3 phases
- `src/strategy.py` — fonctions utilitaires (edge, Signal, WindowInfo, position sizing flat)
- `src/strategies/` — 17 stratégies + combined.py, chacune avec `prefetch()`
- `src/paper_trader.py` — simulation trades avec PnL on-chain réel + final pool snapshot
- `src/live_trader.py` — exécution on-chain BSC (betBull/betBear) + prepare/fire/verify TX
- `src/round_logger.py` — log TOUS les rounds (thread-safe, dédup par epoch)
- `src/pancake.py` — client PancakeSwap V2 + Chainlink oracle
- `src/market_data.py` — feed Binance WebSocket
- `src/dashboard.py` — dashboard Rich
- `scripts/sync-sheets.js` — sync CSV → Google Sheets (cron hourly, fully dynamic)
- `scripts/health-check.py` — diagnostic trading quality (6 checks, PASS/WARN/FAIL)

## Mode Sniper
Architecture 3 phases par epoch :
- **Phase 1 (T-20s→T-3s)** : prefetch données lentes en parallèle (ThreadPoolExecutor)
- **Phase 2 (T-3s→T-1s)** : un poll on-chain frais → evaluate 17 stratégies → fire TX
- **Phase 3 (après lock)** : vérifier confirmation TX (live mode)
- Poll interval : 1s pendant entry window, 5s en dehors
- Config : `sniper_window_seconds: 3`, `min_seconds_before_lock: 1`

## Position Sizing
- Flat $10 par trade, pas de Kelly
- Guards : min $5 (skip si en-dessous), max 10% bankroll, max 10% pool

## Logs / Trades
- Structure : `logs/<mode>/<strategy>/<strategy>.json` + `.csv`
- `logs/rounds/all_rounds.json` + `.csv` — tous les rounds (crowd data)
- JSON = source de vérité, CSV = export pour Google Sheets
- ⚠️ Ne jamais supprimer le JSON sans supprimer le CSV aussi

## Google Sheets
- Sheet ID : `1xJWy6ZLCATaB_RMbe0qKy7ur_dc4_RxZR0TiqR-as80`
- Service account : `service_account.json` (gitignored)
- Cron sync : toutes les heures rondes

## Stratégies (17)
1. GBM Momentum — Monte Carlo
2. Order Book Imbalance v2 — Binance depth 100 niveaux
3. Pool Contrarian — bet contre foule
4. Mean Reversion — z-score momentum
5. Follow the Crowd — suit majorité pool
6. Funding Rate Contrarian — premium mark/index Binance
7. Open Interest Momentum — delta OI
8. Liquidation Reversal — WebSocket forceOrder
9. Volume Profile Breakout — breakout du POC
10. RSI Extreme Reversal — RSI(5)+RSI(14)
11. Order Flow Imbalance — ratio taker buy/sell
12. Market Regime Adaptive — Hurst exponent
13. BTC/ETH Correlation Arbitrage — lag correlation
14. Fear & Greed Micro — MFI basé sur range 24h
15. Whale On-Chain Signal — BSC txs >1000 BNB
16. Bollinger Band Squeeze — squeeze + breakout
17. LLM Price Action — Groq/Llama 3.3-70B (expérimental, rate limited)

### Philosophie stratégies
- Aucun pré-filtre redondant avec l'edge
- Edges distribués [0, 0.50] (sauf follow_crowd/llm: [0, 1.0])
- Tous les clamps p_up/p_win à [0.01, 0.99]
- `use_fair_odds: true` — edge calculé vs 0.50

## Config clés
```
position_size_usdc: 10
min_position_usdc: 5
max_bankroll_pct: 0.10
max_pool_pct: 0.10
edge_threshold: 0.10
entry_window_seconds: 20
sniper_window_seconds: 3
min_seconds_before_lock: 1
use_fair_odds: true
```

## Prochaines étapes
- Accumuler données (objectif 500+ trades/stratégie pour Deep Edge fiable)
- À 500+ trades : analyser Deep Edge + Optimized Combinations
- À ~1000 trades : ML léger (random forest) sur features des stratégies
