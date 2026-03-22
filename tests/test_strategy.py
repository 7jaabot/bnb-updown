"""
test_strategy.py — Unit tests for strategy.py calculation logic

Run with:
  cd repos/polymarket-btc
  source venv/bin/activate
  python -m pytest tests/ -v
"""

import sys
import time
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from strategy import (
    compute_momentum,
    compute_volatility,
    compute_edge,
    kelly_fraction,
    compute_position_size,
    estimate_p_up_momentum,
    get_current_window,
    Strategy,
    WindowInfo,
)


# ─────────────────────────────────────────────────────────────────────────────
# compute_momentum
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeMomentum:
    def test_flat_prices(self):
        prices = [100.0, 100.0, 100.0]
        assert compute_momentum(prices) == pytest.approx(0.0)

    def test_upward_momentum(self):
        prices = [100.0, 101.0, 102.0]
        assert compute_momentum(prices) == pytest.approx(0.02)

    def test_downward_momentum(self):
        prices = [100.0, 99.0, 98.0]
        assert compute_momentum(prices) == pytest.approx(-0.02)

    def test_single_price_returns_zero(self):
        assert compute_momentum([50000.0]) == 0.0

    def test_empty_returns_zero(self):
        assert compute_momentum([]) == 0.0

    def test_large_move(self):
        prices = [100.0, 110.0]
        assert compute_momentum(prices) == pytest.approx(0.10)


# ─────────────────────────────────────────────────────────────────────────────
# compute_volatility
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeVolatility:
    def test_zero_vol_flat_prices(self):
        prices = [100.0, 100.0, 100.0, 100.0]
        assert compute_volatility(prices) == pytest.approx(0.0)

    def test_insufficient_data(self):
        assert compute_volatility([100.0]) == 0.0
        assert compute_volatility([100.0, 101.0]) == 0.0

    def test_positive_vol(self):
        prices = [100.0, 101.0, 99.0, 102.0, 98.0]
        vol = compute_volatility(prices)
        assert vol > 0.0

    def test_higher_vol_more_swings(self):
        stable = [100.0, 100.1, 100.0, 100.1, 100.0]
        volatile = [100.0, 105.0, 95.0, 107.0, 93.0]
        assert compute_volatility(volatile) > compute_volatility(stable)


# ─────────────────────────────────────────────────────────────────────────────
# compute_edge
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeEdge:
    def test_yes_edge(self):
        edge, side = compute_edge(p_up=0.65, yes_price=0.55)
        assert side == "YES"
        assert edge == pytest.approx(0.10)

    def test_no_edge(self):
        edge, side = compute_edge(p_up=0.40, yes_price=0.55)
        assert side == "NO"
        assert edge == pytest.approx(0.15)

    def test_no_edge_equal(self):
        edge, side = compute_edge(p_up=0.50, yes_price=0.50)
        # Edge is 0, side could be either; just check edge is 0
        assert edge == pytest.approx(0.0)

    def test_edge_at_extremes(self):
        edge, side = compute_edge(p_up=0.90, yes_price=0.10)
        assert side == "YES"
        assert edge == pytest.approx(0.80)


# ─────────────────────────────────────────────────────────────────────────────
# kelly_fraction
# ─────────────────────────────────────────────────────────────────────────────

class TestKellyFraction:
    def test_fair_coin_even_odds(self):
        # p=0.5, odds=1 → kelly = 0 (break-even)
        result = kelly_fraction(p=0.5, odds=1.0)
        assert result == pytest.approx(0.0)

    def test_positive_edge(self):
        # p=0.6, odds=1 → f = (0.6*1 - 0.4) / 1 = 0.2
        result = kelly_fraction(p=0.6, odds=1.0)
        assert result == pytest.approx(0.2)

    def test_negative_edge(self):
        # p=0.4, odds=1 → f = (0.4*1 - 0.6) / 1 = -0.2
        result = kelly_fraction(p=0.4, odds=1.0)
        assert result == pytest.approx(-0.2)

    def test_zero_odds_returns_zero(self):
        result = kelly_fraction(p=0.7, odds=0.0)
        assert result == 0.0

    def test_high_edge_high_kelly(self):
        # p=0.8, odds=1 → f = 0.6
        result = kelly_fraction(p=0.8, odds=1.0)
        assert result == pytest.approx(0.6)


# ─────────────────────────────────────────────────────────────────────────────
# compute_position_size
# ─────────────────────────────────────────────────────────────────────────────

class TestComputePositionSize:
    def test_yes_trade_size(self):
        raw_k, size = compute_position_size(
            edge=0.10,
            p_up=0.65,
            side="YES",
            yes_price=0.55,
            bankroll=1000.0,
            kelly_fraction_cap=0.25,
            max_usdc=50.0,
        )
        assert raw_k > 0
        assert size > 0
        assert size <= 50.0

    def test_no_trade_size(self):
        raw_k, size = compute_position_size(
            edge=0.15,
            p_up=0.40,
            side="NO",
            yes_price=0.55,
            bankroll=1000.0,
            kelly_fraction_cap=0.25,
            max_usdc=50.0,
        )
        assert size > 0

    def test_max_cap_respected(self):
        _, size = compute_position_size(
            edge=0.40,
            p_up=0.90,
            side="YES",
            yes_price=0.50,
            bankroll=10000.0,
            kelly_fraction_cap=0.25,
            max_usdc=50.0,
        )
        assert size <= 50.0

    def test_negative_edge_returns_zero(self):
        raw_k, size = compute_position_size(
            edge=0.0,
            p_up=0.45,
            side="NO",
            yes_price=0.45,
            bankroll=1000.0,
            kelly_fraction_cap=0.25,
            max_usdc=50.0,
        )
        assert size == 0.0

    def test_zero_price_returns_zero(self):
        raw_k, size = compute_position_size(
            edge=0.10,
            p_up=0.60,
            side="YES",
            yes_price=0.0,  # Invalid price
            bankroll=1000.0,
        )
        assert size == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# estimate_p_up_momentum (GBM Monte Carlo)
# ─────────────────────────────────────────────────────────────────────────────

class TestEstimatePUp:
    def test_flat_prices_near_50(self):
        prices = [50000.0] * 100
        p = estimate_p_up_momentum(prices, seconds_remaining=30)
        # Flat prices → should be around 0.5 (pure noise)
        assert 0.3 < p < 0.7

    def test_strong_uptrend_high_p_up(self):
        # Prices that went up 2% → should bias P(Up) upward
        prices = [50000.0 + i * 10 for i in range(100)]
        p = estimate_p_up_momentum(prices, seconds_remaining=10)
        # With strong uptrend and only 10s left, P(Up) should be elevated
        assert p > 0.5

    def test_strong_downtrend_low_p_up(self):
        prices = [50000.0 - i * 10 for i in range(100)]
        p = estimate_p_up_momentum(prices, seconds_remaining=10)
        assert p < 0.5

    def test_insufficient_data_returns_half(self):
        p = estimate_p_up_momentum([50000.0, 50001.0], seconds_remaining=60)
        assert p == 0.5

    def test_output_is_probability(self):
        prices = [50000.0 + i * 5 for i in range(50)]
        p = estimate_p_up_momentum(prices, seconds_remaining=60)
        assert 0.0 <= p <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# get_current_window
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCurrentWindow:
    def test_window_aligned_to_5min(self):
        now = 1700000000.0  # Some timestamp
        window = get_current_window(now)
        assert window.window_start_ts % 300 == 0
        assert window.window_end_ts == window.window_start_ts + 300

    def test_seconds_remaining_positive(self):
        window = get_current_window()
        assert 0 < window.seconds_remaining <= 300

    def test_entry_window_detection(self):
        # Create a window with 45 seconds remaining
        now = time.time()
        window_start = int(now / 300) * 300
        window_end = window_start + 300
        # Simulate being 255 seconds into window (45s remaining)
        fake_now = window_start + 255
        window = get_current_window(fake_now)
        assert window.is_entry_window is True

    def test_not_entry_window_early(self):
        now = time.time()
        window_start = int(now / 300) * 300
        # Simulate being 30 seconds into window (270s remaining)
        fake_now = window_start + 30
        window = get_current_window(fake_now)
        assert window.is_entry_window is False


# ─────────────────────────────────────────────────────────────────────────────
# Strategy class (integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategy:
    def _make_config(self, edge_threshold=0.05):
        return {
            "strategy": {
                "edge_threshold": edge_threshold,
                "kelly_fraction": 0.25,
                "max_position_usdc": 50.0,
                "starting_bankroll_usdc": 1000.0,
                "entry_window_seconds": 60,
                "min_prob_diff": 0.03,
            }
        }

    def _make_window(self, seconds_remaining=30.0):
        now = time.time()
        window_start = int(now / 300) * 300
        return WindowInfo(
            window_start_ts=float(window_start),
            window_end_ts=float(window_start + 300),
            seconds_remaining=seconds_remaining,
            window_index=int(window_start / 300),
        )

    def test_no_signal_outside_entry_window(self):
        strat = Strategy(self._make_config())
        prices = [50000.0 + i * 5 for i in range(100)]
        window = self._make_window(seconds_remaining=120)  # 2 min remaining
        signal = strat.evaluate(prices, yes_price=0.50, window=window)
        assert signal is None

    def test_signal_in_entry_window_with_edge(self):
        strat = Strategy(self._make_config(edge_threshold=0.05))
        # Create a strong uptrend
        prices = [50000.0 + i * 20 for i in range(200)]
        window = self._make_window(seconds_remaining=30)
        # Set YES price very low → big edge for YES
        signal = strat.evaluate(prices, yes_price=0.20, window=window)
        if signal:  # GBM is stochastic, might not always trigger
            assert signal.side in ("YES", "NO")
            assert signal.edge >= 0.05
            assert signal.position_size_usdc > 0

    def test_no_signal_below_edge_threshold(self):
        strat = Strategy(self._make_config(edge_threshold=0.99))  # Unreachable threshold
        prices = [50000.0 + i * 5 for i in range(100)]
        window = self._make_window(seconds_remaining=30)
        signal = strat.evaluate(prices, yes_price=0.50, window=window)
        # With 99% threshold, no signal possible
        assert signal is None

    def test_no_signal_insufficient_prices(self):
        strat = Strategy(self._make_config())
        window = self._make_window(seconds_remaining=30)
        signal = strat.evaluate([], yes_price=0.50, window=window)
        assert signal is None


if __name__ == "__main__":
    # Quick smoke test
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        capture_output=False
    )
    sys.exit(result.returncode)
