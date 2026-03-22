"""
prdt.py — PRDT Finance client for BTC/USD 5-minute prediction markets

PRDT Classic mode: binary pool (bull vs bear).
Each round:
  - Lock period: entry open
  - End: settlement via Chainlink oracle
  - Winners split the losers' pool (minus ~3% fee)

Key difference vs Polymarket:
  - No YES/NO price from an order book
  - Edge comes from the bull/bear pool ratio:
    if pool is 70% bull → bear pays out 70/30 = 2.3x → bear is +EV if P(down) > ~43%

Smart contracts (Polygon):
  0x0b9c8c0a04354f41b985c10daf7db30bc66998f5

Data is read directly from on-chain via a public RPC (no API key needed).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("PRDTClient")

# Polygon public RPC endpoints (no key needed)
POLYGON_RPC_URLS = [
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://rpc-mainnet.matic.network",
]

# PRDT Classic contract on Polygon
PRDT_CONTRACT_POLYGON = "0x0b9c8c0a04354f41b985c10daf7db30bc66998f5"

# Minimal ABI for reading current round data
PRDT_ABI = [
    {
        "inputs": [],
        "name": "currentEpoch",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "epoch", "type": "uint256"}],
        "name": "rounds",
        "outputs": [
            {"name": "epoch", "type": "uint256"},
            {"name": "startTimestamp", "type": "uint256"},
            {"name": "lockTimestamp", "type": "uint256"},
            {"name": "closeTimestamp", "type": "uint256"},
            {"name": "lockPrice", "type": "int256"},
            {"name": "closePrice", "type": "int256"},
            {"name": "lockOracleId", "type": "uint256"},
            {"name": "closeOracleId", "type": "uint256"},
            {"name": "totalAmount", "type": "uint256"},
            {"name": "bullAmount", "type": "uint256"},
            {"name": "bearAmount", "type": "uint256"},
            {"name": "rewardBaseCalAmount", "type": "uint256"},
            {"name": "rewardAmount", "type": "uint256"},
            {"name": "oracleCalled", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "intervalSeconds",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "bufferSeconds",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
]


@dataclass
class PRDTRound:
    epoch: int
    start_ts: int
    lock_ts: int
    close_ts: int
    total_amount: float       # total USDT in pool (18 decimals normalized)
    bull_amount: float        # total bet UP
    bear_amount: float        # total bet DOWN
    bull_ratio: float         # bull_amount / total_amount
    bear_ratio: float         # bear_amount / total_amount
    bull_payout: float        # if bear wins → bull_payout = total/bear
    bear_payout: float        # if bull wins → bear_payout = total/bull
    lock_price: Optional[float]
    is_mock: bool = False

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.close_ts - time.time())

    @property
    def is_open(self) -> bool:
        now = time.time()
        return self.start_ts <= now < self.lock_ts


def _mock_round() -> PRDTRound:
    """Generate a plausible mock round when on-chain read fails."""
    import random
    now = int(time.time())
    # Simulate a 5-minute window
    start = (now // 300) * 300
    lock = start + 270   # lock 30s before close
    close = start + 300

    bull_pct = random.uniform(0.35, 0.65)
    bear_pct = 1.0 - bull_pct
    total = random.uniform(500, 5000)
    bull_amt = total * bull_pct
    bear_amt = total * bear_pct
    fee = 0.03

    bull_payout = (total * (1 - fee)) / bull_amt if bull_amt > 0 else 2.0
    bear_payout = (total * (1 - fee)) / bear_amt if bear_amt > 0 else 2.0

    return PRDTRound(
        epoch=now // 300,
        start_ts=start,
        lock_ts=lock,
        close_ts=close,
        total_amount=total,
        bull_amount=bull_amt,
        bear_amount=bear_amt,
        bull_ratio=bull_pct,
        bear_ratio=bear_pct,
        bull_payout=bull_payout,
        bear_payout=bear_payout,
        lock_price=None,
        is_mock=True,
    )


class PRDTClient:
    """
    Reads PRDT Finance Classic contract state from Polygon.

    Uses web3.py if available, falls back to raw JSON-RPC calls,
    falls back to mock data if chain is unreachable.
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        contract_address: str = PRDT_CONTRACT_POLYGON,
        use_mock_on_failure: bool = True,
        timeout: int = 10,
    ):
        self.rpc_url = rpc_url or POLYGON_RPC_URLS[0]
        self.contract_address = contract_address
        self.use_mock_on_failure = use_mock_on_failure
        self.timeout = timeout
        self._w3 = None
        self._contract = None
        self._web3_available = False
        self._init_web3()

    def _init_web3(self):
        try:
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": self.timeout}))
            if w3.is_connected():
                self._w3 = w3
                self._contract = w3.eth.contract(
                    address=Web3.to_checksum_address(self.contract_address),
                    abi=PRDT_ABI,
                )
                self._web3_available = True
                logger.info(f"✅ PRDT: web3 connected to Polygon ({self.rpc_url})")
            else:
                logger.warning("PRDT: web3 connected but chain unreachable")
        except ImportError:
            logger.warning("PRDT: web3.py not installed — install with: pip install web3")
        except Exception as e:
            logger.warning(f"PRDT: web3 init failed: {e}")

    def check_connectivity(self) -> bool:
        if self._web3_available and self._w3:
            try:
                self._w3.eth.block_number
                return True
            except Exception:
                pass
        return False

    def get_current_round(self) -> Optional[PRDTRound]:
        """Fetch current round state from the contract."""
        if self._web3_available and self._contract:
            try:
                return self._fetch_round_onchain()
            except Exception as e:
                logger.warning(f"PRDT: on-chain read failed: {e}")

        if self.use_mock_on_failure:
            logger.debug("PRDT: using mock round")
            return _mock_round()
        return None

    def _fetch_round_onchain(self) -> PRDTRound:
        epoch = self._contract.functions.currentEpoch().call()
        r = self._contract.functions.rounds(epoch).call()

        # r indices: epoch, startTs, lockTs, closeTs, lockPrice, closePrice,
        #            lockOracleId, closeOracleId, totalAmount, bullAmount,
        #            bearAmount, rewardBaseCalAmount, rewardAmount, oracleCalled
        DECIMALS = 1e18
        total = r[8] / DECIMALS
        bull = r[9] / DECIMALS
        bear = r[10] / DECIMALS
        fee = 0.03

        bull_ratio = bull / total if total > 0 else 0.5
        bear_ratio = bear / total if total > 0 else 0.5

        bull_payout = (total * (1 - fee)) / bull if bull > 0 else 2.0
        bear_payout = (total * (1 - fee)) / bear if bear > 0 else 2.0

        lock_price = r[4] / 1e8 if r[4] != 0 else None  # Chainlink 8 decimals

        return PRDTRound(
            epoch=r[0],
            start_ts=r[1],
            lock_ts=r[2],
            close_ts=r[3],
            total_amount=total,
            bull_amount=bull,
            bear_amount=bear,
            bull_ratio=bull_ratio,
            bear_ratio=bear_ratio,
            bull_payout=bull_payout,
            bear_payout=bear_payout,
            lock_price=lock_price,
            is_mock=False,
        )
