"""
market_data.py — Binance WebSocket BTC/USDT price feed

Subscribes to wss://stream.binance.com:9443/ws/btcusdt@trade
and maintains a rolling buffer of recent trade prices.
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import Optional, Callable

import websockets

logger = logging.getLogger(__name__)


class PricePoint:
    """Single BTC trade price with timestamp."""

    def __init__(self, price: float, timestamp: float):
        self.price = price
        self.timestamp = timestamp  # Unix epoch seconds (float)

    def __repr__(self):
        return f"PricePoint(price={self.price:.2f}, ts={self.timestamp:.3f})"


class BinanceFeed:
    """
    Real-time BTC/USDT trade feed from Binance WebSocket.

    Maintains a rolling deque of PricePoint objects.
    Fires an optional on_price callback on each new trade.
    """

    WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"

    def __init__(
        self,
        buffer_seconds: int = 310,
        on_price: Optional[Callable[[PricePoint], None]] = None,
    ):
        """
        Args:
            buffer_seconds: How many seconds of history to retain.
            on_price: Optional callback called with each new PricePoint.
        """
        self.buffer_seconds = buffer_seconds
        self.on_price = on_price
        self._prices: deque[PricePoint] = deque()
        self._running = False
        self._last_price: Optional[float] = None

    @property
    def last_price(self) -> Optional[float]:
        """Most recent BTC price, or None if no data yet."""
        return self._last_price

    def get_prices_since(self, seconds_ago: float) -> list[PricePoint]:
        """Return all price points within the last N seconds."""
        cutoff = time.time() - seconds_ago
        return [p for p in self._prices if p.timestamp >= cutoff]

    def get_window_prices(self, start_ts: float, end_ts: Optional[float] = None) -> list[PricePoint]:
        """Return prices within [start_ts, end_ts]."""
        if end_ts is None:
            end_ts = time.time()
        return [p for p in self._prices if start_ts <= p.timestamp <= end_ts]

    def _add_price(self, price: float, timestamp: float):
        """Add a new price point and prune old entries."""
        pp = PricePoint(price, timestamp)
        self._prices.append(pp)
        self._last_price = price

        # Prune entries older than buffer_seconds
        cutoff = timestamp - self.buffer_seconds
        while self._prices and self._prices[0].timestamp < cutoff:
            self._prices.popleft()

        if self.on_price:
            try:
                self.on_price(pp)
            except Exception as e:
                logger.warning(f"on_price callback error: {e}")

    async def run(self):
        """Main async loop — connect and stream trades until stopped."""
        self._running = True
        logger.info(f"Connecting to Binance WebSocket: {self.WS_URL}")

        reconnect_delay = 1.0

        while self._running:
            try:
                async with websockets.connect(self.WS_URL, ping_interval=20) as ws:
                    logger.info("Binance WebSocket connected.")
                    reconnect_delay = 1.0  # Reset on successful connection

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            # Binance trade stream format:
                            # {"e": "trade", "T": <ms timestamp>, "p": "<price>", ...}
                            if msg.get("e") == "trade":
                                price = float(msg["p"])
                                ts = msg["T"] / 1000.0  # ms → seconds
                                self._add_price(price, ts)
                        except (KeyError, ValueError, json.JSONDecodeError) as e:
                            logger.debug(f"Skipping malformed message: {e}")

            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                if self._running:
                    logger.warning(
                        f"Binance WS disconnected: {e}. "
                        f"Reconnecting in {reconnect_delay:.1f}s..."
                    )
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)

            except Exception as e:
                logger.error(f"Unexpected Binance WS error: {e}", exc_info=True)
                if self._running:
                    await asyncio.sleep(reconnect_delay)

    def stop(self):
        """Signal the feed to stop."""
        self._running = False
        logger.info("BinanceFeed stopping.")
