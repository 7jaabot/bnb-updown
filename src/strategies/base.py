"""
base.py — Abstract base class for all trading strategies.

All strategies share a common interface: evaluate() returns a Signal or None.
Utility functions (Kelly, edge, position sizing) live in strategy.py.
"""

import sys
import os

# Ensure parent src/ is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from abc import ABC, abstractmethod
from typing import Optional

from strategy import Signal, WindowInfo


class BaseStrategy(ABC):
    """Interface commune pour toutes les stratégies de trading."""

    def __init__(self, config: dict):
        cfg = config.get("strategy", {})
        self.edge_threshold = cfg.get("edge_threshold", 0.10)
        self.kelly_fraction_cap = cfg.get("kelly_fraction", 0.25)
        self.max_position_usdc = cfg.get("max_position_usdc", 15.0)
        self.entry_window_seconds = cfg.get("entry_window_seconds", 25)
        self.bankroll = cfg.get("starting_bankroll_usdc", 1000.0)
        self.last_skip_reason: Optional[str] = None

        pcfg = config.get("pancake", {})
        self.min_pool_bnb = pcfg.get("min_pool_bnb", 0.4)
        self.max_bet_share_of_side = pcfg.get("max_bet_share_of_side", 0.25)
        self.pancake_fee = pcfg.get("fee", 0.03)

    def update_bankroll(self, new_bankroll: float):
        """Update the current bankroll after PnL changes."""
        self.bankroll = new_bankroll
        self.last_skip_reason = None

    @abstractmethod
    def evaluate(
        self,
        prices: list[float],
        yes_price: float,
        window: WindowInfo,
        is_mock_data: bool = False,
        pool_total_bnb: float = 0.0,
        pool_bull_bnb: float = 0.0,
        pool_bear_bnb: float = 0.0,
    ) -> Optional[Signal]:
        """Evaluate and return a Signal or None."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name for display."""
        ...
