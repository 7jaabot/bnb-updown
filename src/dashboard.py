"""
dashboard.py — Rich-based live dashboard for BNB Up/Down Trading Bot

Displays a compact, auto-refreshing terminal UI with:
  - Status panel: mode, BNB price, balance, PnL, ROI, win rate, window info
  - Recent trades table (last 10 trades)
  - Scrolling activity log (last 8 lines shown, 20 buffered)

Usage:
    dashboard = Dashboard(trader=trader, binance=binance_feed, mode="paper")
    live = dashboard.start()   # returns a rich.live.Live instance
    # Use as context manager in your run() method:
    async with dashboard.start() as live:
        ...
        live.update(dashboard.render())   # call to refresh display
"""

import time
from collections import deque
from datetime import datetime
from typing import Any, Optional

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class Dashboard:
    """
    Rich live dashboard for the BNB Up/Down trading bot.

    Works with both PaperTrader and LiveTrader — reads from trader.metrics
    and trader._trades using duck typing.
    """

    def __init__(self, trader: Any, binance: Any, mode: str):
        """
        Args:
            trader: PaperTrader or LiveTrader instance.
            binance: BinanceFeed instance (for last_price).
            mode: "paper" or "live".
        """
        self.trader = trader
        self.binance = binance
        self.mode = mode  # "paper" or "live"

        # Activity log: circular buffer, display last 8 of 20 stored
        self._log_buffer: deque[str] = deque(maxlen=20)

        # Current status text (updated by main loop)
        self._status: str = "⏳ Waiting for entry window"

        # Current window info (updated on each tick)
        self._current_window: Optional[Any] = None

        # Cached live BNB balance (avoid blocking web3 calls in render)
        self._cached_bnb_balance: Optional[float] = None
        self._last_balance_update: float = 0.0

    # ─── Public API ──────────────────────────────────────────────────────────

    def log(self, message: str) -> None:
        """Add a timestamped message to the scrolling activity log."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_buffer.append(f"[{ts}] {message}")

    def update_status(self, status_text: str) -> None:
        """Update the current status text shown in the status panel."""
        self._status = status_text

    def update_window(self, window: Any) -> None:
        """Update the current window info for display."""
        self._current_window = window

    def start(self) -> Live:
        """
        Create and return a Rich Live context manager.

        Usage:
            async with dashboard.start() as live:
                live.update(dashboard.render())
        """
        return Live(
            self.render(),
            refresh_per_second=0.5,  # auto-refresh every 2s as fallback
            auto_refresh=True,
            screen=False,
            transient=False,
        )

    # ─── Panel builders ──────────────────────────────────────────────────────

    def _make_status_panel(self) -> Panel:
        """Build the top status panel with mode, price, balance, PnL, window."""
        metrics = self.trader.metrics
        bnb_price = self.binance.last_price

        # ── Line 1: Mode + wallet ──
        if self.mode == "live":
            mode_str = "[bold red]🔴 LIVE[/bold red]"
            wallet = getattr(self.trader, "_wallet_address", None)
            if wallet and len(wallet) > 10:
                wallet_short = f"{wallet[:6]}...{wallet[-4:]}"
            else:
                wallet_short = wallet or "N/A"
            first_line = f"Mode: {mode_str}  |  Wallet: {wallet_short}"
        else:
            mode_str = "[bold blue]📝 PAPER[/bold blue]"
            first_line = f"Mode: {mode_str}"

        # ── Line 2: BNB price + balance ──
        price_str = f"${bnb_price:.2f}" if bnb_price is not None else "[yellow]N/A[/yellow]"

        if self.mode == "live":
            if self._cached_bnb_balance is not None and bnb_price:
                bnb_usdc = self._cached_bnb_balance * bnb_price
                balance_str = f"{self._cached_bnb_balance:.4f} BNB (${bnb_usdc:.2f})"
            elif self._cached_bnb_balance is not None:
                balance_str = f"{self._cached_bnb_balance:.4f} BNB"
            else:
                balance_str = "[yellow]loading...[/yellow]"
        else:
            balance_str = f"${metrics.bankroll:.2f}"

        price_line = f"BNB Price: {price_str}  |  Balance: {balance_str}"

        # ── Line 3: empty ──

        # ── Line 4: PnL / ROI / Win Rate ──
        pnl = metrics.total_pnl
        pnl_color = "green" if pnl >= 0 else "red"
        pnl_str = f"[{pnl_color}]${pnl:+.2f}[/{pnl_color}]"

        roi = metrics.roi * 100
        roi_color = "green" if roi >= 0 else "red"
        roi_str = f"[{roi_color}]{roi:+.1f}%[/{roi_color}]"

        wins = metrics.wins
        losses = metrics.losses
        win_rate = metrics.win_rate * 100
        pnl_line = (
            f"PnL: {pnl_str}  |  ROI: {roi_str}  |  "
            f"Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)"
        )

        # ── Line 5: Wagered / Avg Edge / Daily Loss (live only) ──
        wagered = metrics.total_wagered
        avg_edge = metrics.avg_edge

        if self.mode == "live":
            max_daily = getattr(self.trader, "max_daily_loss_usdc", 100.0)
            daily_pnl = getattr(metrics, "daily_pnl", 0.0)
            daily_left = max_daily + daily_pnl  # positive when budget remains
            if daily_left > max_daily * 0.5:
                daily_color = "green"
            elif daily_left > 0:
                daily_color = "yellow"
            else:
                daily_color = "red"
            daily_str = f"  |  Daily Loss Left: [{daily_color}]${daily_left:.2f}[/{daily_color}]"
        else:
            daily_str = ""

        wager_line = f"Wagered: ${wagered:.2f}  |  Avg Edge: {avg_edge:.3f}{daily_str}"

        # ── Line 6: empty ──

        # ── Line 7: Epoch / Lock in / Phase / Status ──
        if self._current_window is not None:
            w = self._current_window
            lock_in = int(w.seconds_remaining)

            # Determine phase label
            entry_secs = getattr(w, 'entry_window_seconds', 60.0)
            if lock_in <= 0:
                phase = "[bold red]LOCKED[/bold red]"
            elif lock_in <= entry_secs:
                phase = "[bold yellow]EVALUATING[/bold yellow]"
            else:
                phase = "[bold green]BET OPEN[/bold green]"

            window_line = (
                f"Epoch: #{w.window_index}  |  Lock in: {lock_in}s  |  "
                f"Phase: {phase}  |  Status: {self._status}"
            )
        else:
            window_line = f"Status: {self._status}"

        content = Text.from_markup(
            f"{first_line}\n"
            f"{price_line}\n"
            f"\n"
            f"{pnl_line}\n"
            f"{wager_line}\n"
            f"\n"
            f"{window_line}"
        )

        return Panel(
            content,
            title="[bold cyan]BNB Up/Down Trading Bot[/bold cyan]",
            border_style="cyan",
        )

    def _make_trades_table(self) -> Panel:
        """Build the recent trades table (last 10, most recent first)."""
        table = Table(
            show_header=True,
            header_style="bold magenta",
            box=None,
            padding=(0, 1),
            show_edge=False,
        )
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Time", width=7)
        table.add_column("Side", width=6)
        table.add_column("Edge", width=6)
        table.add_column("PnL", width=9, justify="right")

        all_trades = list(self.trader._trades)
        recent = list(reversed(all_trades[-10:]))  # most recent first

        for i, trade in enumerate(recent):
            trade_num = len(all_trades) - i

            ts = datetime.fromtimestamp(trade.timestamp_entry).strftime("%H:%M")

            side = trade.side
            side_color = "green" if side == "YES" else "red"
            side_str = f"[{side_color}]{side}[/{side_color}]"

            edge_str = f"{trade.edge_at_entry:.2f}"

            if trade.pnl_usdc is not None:
                pnl_color = "green" if trade.pnl_usdc >= 0 else "red"
                pnl_str = f"[{pnl_color}]{trade.pnl_usdc:+.1f}[/{pnl_color}]"
            else:
                pnl_str = "[yellow]…[/yellow]"

            table.add_row(str(trade_num), ts, side_str, edge_str, pnl_str)

        if not recent:
            table.add_row("[dim]-[/dim]", "[dim]-[/dim]", "[dim]-[/dim]", "[dim]-[/dim]", "[dim]-[/dim]")

        return Panel(
            table,
            title="[bold magenta]Recent Trades[/bold magenta]",
            border_style="magenta",
        )

    def _make_log_panel(self) -> Panel:
        """Build the scrolling activity log panel."""
        lines = list(self._log_buffer)[-8:]  # show last 8 of 20 buffered
        if lines:
            content = "\n".join(lines)
        else:
            content = "[dim]No activity yet...[/dim]"

        return Panel(
            Text.from_markup(content),
            title="[bold yellow]Activity Log[/bold yellow]",
            border_style="yellow",
        )

    def render(self) -> Group:
        """Return a Rich renderable for the full dashboard."""
        return Group(
            self._make_status_panel(),
            self._make_trades_table(),
            self._make_log_panel(),
        )
