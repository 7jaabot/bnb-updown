#!/usr/bin/env python3
"""
BNB-UPDOWN Health Check
Diagnostic script to verify trading quality across strategies.

Usage:
    python scripts/health-check.py --mode paper
    python scripts/health-check.py --mode paper --strategy follow_crowd
    python scripts/health-check.py --mode paper --from-epoch 468057
    python scripts/health-check.py --mode paper --json
    python scripts/health-check.py --mode paper --warnings-only
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Try rich for pretty output
try:
    from rich.console import Console
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_NA   = "N/A"

STATUS_COLORS = {
    STATUS_PASS: "green",
    STATUS_WARN: "yellow",
    STATUS_FAIL: "red",
    STATUS_NA:   "dim",
}

STATUS_ICONS = {
    STATUS_PASS: "✅",
    STATUS_WARN: "⚠️ ",
    STATUS_FAIL: "❌",
    STATUS_NA:   "➖",
}

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def safe_mean(values):
    return sum(values) / len(values) if values else 0.0

def safe_median(values):
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2

def wilson_ci(wins, n, z=1.96):
    """Wilson score confidence interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - spread), min(1.0, center + spread))

def repo_root():
    """Return repo root (works when called from any cwd)."""
    script_dir = Path(__file__).parent.parent
    # If invoked from repo root directly, __file__ might be scripts/health-check.py
    return script_dir

# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def load_trades(mode: str, strategy: str, from_epoch: int | None = None) -> list[dict]:
    """Load trades for a given strategy from the JSON log file."""
    root = repo_root()
    json_path = root / "logs" / mode / strategy / f"{strategy}.json"
    if not json_path.exists():
        return []
    try:
        with open(json_path) as f:
            data = json.load(f)
        trades = data.get("trades", [])
        if from_epoch is not None:
            trades = [t for t in trades if t.get("epoch", 0) >= from_epoch]
        return trades
    except Exception:
        return []

def list_strategies(mode: str) -> list[str]:
    """List all strategies that have a JSON trade log."""
    root = repo_root()
    logs_dir = root / "logs" / mode
    if not logs_dir.exists():
        return []
    strategies = []
    for d in sorted(logs_dir.iterdir()):
        if d.is_dir():
            json_file = d / f"{d.name}.json"
            if json_file.exists():
                strategies.append(d.name)
    return strategies

def parse_run_logs(mode: str, strategy: str | None = None) -> list[str]:
    """
    Return all log lines from run logs for a given strategy (or top-level mode logs).
    Reads ALL run-*.log files available (not just today's).
    """
    root = repo_root()
    if strategy:
        log_dir = root / "logs" / mode / strategy
    else:
        log_dir = root / "logs" / mode

    lines = []
    for log_file in sorted(log_dir.glob("run-*.log")):
        try:
            with open(log_file) as f:
                lines.extend(f.readlines())
        except Exception:
            pass
    return lines

def parse_combined_constituents(strategy_name: str, known_strategies: list[str]) -> list[str]:
    """
    Extract constituent strategy names from a combined strategy name.
    e.g. 'combined_follow_crowd_order_flow_415621' → ['follow_crowd', 'order_flow']
    """
    if not strategy_name.startswith("combined_"):
        return []
    # Remove "combined_" prefix
    remainder = strategy_name[len("combined_"):]
    # Remove trailing numeric ID (e.g. "_415621")
    remainder = re.sub(r"_\d+$", "", remainder)

    # Greedily match known strategies from left to right
    # Sort by length descending to prefer longer matches
    sorted_strategies = sorted(known_strategies, key=len, reverse=True)
    found = []
    while remainder:
        matched = False
        for s in sorted_strategies:
            if remainder == s or remainder.startswith(s + "_"):
                found.append(s)
                remainder = remainder[len(s):].lstrip("_")
                matched = True
                break
        if not matched:
            # Give up — can't parse
            break
    return found

# ──────────────────────────────────────────────────────────────────────────────
# Check 1: Timing Quality
# ──────────────────────────────────────────────────────────────────────────────

def check_timing_quality(all_trades: dict[str, list], all_log_lines: list[str]) -> dict:
    """
    Analyze timing quality: seconds before lock at entry, sniper window hits, skips.
    """
    seconds_before_lock = []
    sniper_hits = 0    # 4s ≤ time_before_lock ≤ 7s
    too_early = 0      # > 7s before lock
    total = 0

    # Find the earliest trade timestamp to bound log scanning
    min_trade_ts = None
    for trades in all_trades.values():
        for t in trades:
            ts = t.get("timestamp_entry")
            if ts and (min_trade_ts is None or ts < min_trade_ts):
                min_trade_ts = ts

    for strategy, trades in all_trades.items():
        for t in trades:
            wend = t.get("window_end_ts")
            tentry = t.get("timestamp_entry")
            if wend and tentry:
                sbl = wend - tentry
                seconds_before_lock.append(sbl)
                total += 1
                if 4.0 <= sbl <= 7.0:
                    sniper_hits += 1
                elif sbl > 7.0:
                    too_early += 1

    # Count skip messages from logs — filter to the relevant time window
    # Log format: "2026-03-29 13:36:04,177 [WARNING] ..."
    skip_count = 0
    for line in all_log_lines:
        if re.search(r"Too close to lock|Sniper too late|Too late", line, re.IGNORECASE):
            # If we have a min trade timestamp, only count skips from that time onwards
            if min_trade_ts is not None:
                m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if m:
                    try:
                        log_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                        if log_ts < min_trade_ts - 60:  # 1 minute grace
                            continue
                    except ValueError:
                        pass
            skip_count += 1

    if total == 0:
        return {
            "status": STATUS_NA,
            "details": {"total_trades": 0, "skip_count": skip_count},
            "lines": ["No trades with timing data found."],
        }

    avg_sbl = safe_mean(seconds_before_lock)
    sniper_pct = 100.0 * sniper_hits / total
    early_pct = 100.0 * too_early / total
    skip_rate_pct = 100.0 * skip_count / (total + skip_count) if (total + skip_count) > 0 else 0.0

    # Status
    if avg_sbl < 7.0 and skip_rate_pct < 5.0:
        status = STATUS_PASS
    elif avg_sbl > 10.0 or skip_rate_pct > 15.0:
        status = STATUS_FAIL
    else:
        status = STATUS_WARN

    lines = [
        f"Avg entry: {avg_sbl:.1f}s before lock",
        f"Sniper window hits (4-7s): {sniper_pct:.1f}%",
        f"Too-early trades (>7s): {early_pct:.1f}%",
        f"Too-late skips: {skip_count} ({skip_rate_pct:.1f}% of attempts)",
        f"Total timed trades: {total}",
    ]

    return {
        "status": status,
        "details": {
            "avg_seconds_before_lock": round(avg_sbl, 2),
            "sniper_window_pct": round(sniper_pct, 2),
            "too_early_pct": round(early_pct, 2),
            "skip_count": skip_count,
            "skip_rate_pct": round(skip_rate_pct, 2),
            "total_trades": total,
        },
        "lines": lines,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Check 2: Pool Drift
# ──────────────────────────────────────────────────────────────────────────────

def check_pool_drift(all_trades: dict[str, list]) -> dict:
    """Analyze pool_drift_pct across all strategies."""
    drift_values = []
    drift_by_strategy = {}
    max_drift = 0.0
    max_drift_epoch = None
    max_drift_strategy = None
    gt5 = 0
    gt10 = 0
    total = 0

    for strategy, trades in all_trades.items():
        strat_drifts = []
        for t in trades:
            drift = t.get("pool_drift_pct", 0.0)
            if drift is None:
                drift = 0.0
            # Only count if final_bull_pct is available (drift is meaningful)
            final_bull = t.get("final_bull_pct", 0.0)
            if final_bull and final_bull != 0.0:
                strat_drifts.append(drift)
                drift_values.append(drift)
                total += 1
                if drift > max_drift:
                    max_drift = drift
                    max_drift_epoch = t.get("epoch")
                    max_drift_strategy = strategy
                if drift > 0.05:
                    gt5 += 1
                if drift > 0.10:
                    gt10 += 1
        if strat_drifts:
            drift_by_strategy[strategy] = {
                "avg": round(safe_mean(strat_drifts), 4),
                "median": round(safe_median(strat_drifts), 4),
                "max": round(max(strat_drifts), 4),
                "count": len(strat_drifts),
            }

    if total == 0:
        return {
            "status": STATUS_NA,
            "details": {"total_trades_with_drift": 0},
            "lines": ["No trades with final pool data (pool_drift_pct) found."],
        }

    avg_drift = safe_mean(drift_values)
    avg_drift_pct = avg_drift * 100
    med_drift_pct = safe_median(drift_values) * 100
    max_drift_pct = max_drift * 100
    gt5_pct = 100.0 * gt5 / total
    gt10_pct = 100.0 * gt10 / total

    if avg_drift < 0.03:
        status = STATUS_PASS
    elif avg_drift > 0.08:
        status = STATUS_FAIL
    else:
        status = STATUS_WARN

    lines = [
        f"Avg drift: {avg_drift_pct:.1f}%",
        f"Median drift: {med_drift_pct:.1f}%",
        f"Max drift: {max_drift_pct:.1f}%"
        + (f" (epoch {max_drift_epoch}, {max_drift_strategy})" if max_drift_epoch else ""),
        f"Trades with drift > 5%: {gt5_pct:.1f}%",
        f"Trades with drift > 10%: {gt10_pct:.1f}% (potential wrong direction)",
        f"Total trades with resolved drift: {total}",
    ]

    return {
        "status": status,
        "details": {
            "avg_drift_pct": round(avg_drift_pct, 2),
            "median_drift_pct": round(med_drift_pct, 2),
            "max_drift_pct": round(max_drift_pct, 2),
            "max_drift_epoch": max_drift_epoch,
            "max_drift_strategy": max_drift_strategy,
            "pct_gt5": round(gt5_pct, 2),
            "pct_gt10": round(gt10_pct, 2),
            "total_trades": total,
            "by_strategy": drift_by_strategy,
        },
        "lines": lines,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Check 3: Consistency (combined strategies)
# ──────────────────────────────────────────────────────────────────────────────

def check_consistency(all_trades: dict[str, list], known_strategies: list[str]) -> dict:
    """
    For combined strategies: check if constituent strategies' bull_pct agree at same epoch.
    """
    combined_strategies = [s for s in all_trades if s.startswith("combined_")]

    if not combined_strategies:
        return {
            "status": STATUS_NA,
            "details": {"combined_strategies": []},
            "lines": ["No combined strategies found."],
        }

    # Build epoch → bull_pct lookup for each base strategy
    epoch_lookup: dict[str, dict[int, float]] = {}
    for strategy, trades in all_trades.items():
        if not strategy.startswith("combined_"):
            idx = {}
            for t in trades:
                ep = t.get("epoch")
                bp = t.get("bull_pct")
                if ep and bp is not None:
                    idx[ep] = bp
            epoch_lookup[strategy] = idx

    total_checked = 0
    total_inconsistent = 0
    inconsistent_examples = []
    results_by_combined = {}

    for combined in combined_strategies:
        constituents = parse_combined_constituents(combined, known_strategies)
        if len(constituents) < 2:
            continue

        trades = all_trades.get(combined, [])
        checked = 0
        inconsistent = 0

        for t in trades:
            epoch = t.get("epoch")
            if not epoch:
                continue

            bp_values = []
            for c in constituents:
                bp = epoch_lookup.get(c, {}).get(epoch)
                if bp is not None:
                    bp_values.append(bp)

            if len(bp_values) < 2:
                continue

            checked += 1
            # Check all pairwise within 2%
            is_consistent = all(
                abs(bp_values[i] - bp_values[j]) <= 0.02
                for i in range(len(bp_values))
                for j in range(i + 1, len(bp_values))
            )
            if not is_consistent:
                inconsistent += 1
                if len(inconsistent_examples) < 3:
                    inconsistent_examples.append({
                        "epoch": epoch,
                        "combined": combined,
                        "constituents": dict(zip(constituents, bp_values)),
                    })

        total_checked += checked
        total_inconsistent += inconsistent

        inconsistent_pct = 100.0 * inconsistent / checked if checked > 0 else 0.0
        results_by_combined[combined] = {
            "checked": checked,
            "inconsistent": inconsistent,
            "inconsistent_pct": round(inconsistent_pct, 2),
            "constituents": constituents,
        }

    if total_checked == 0:
        return {
            "status": STATUS_NA,
            "details": {"combined_strategies": combined_strategies},
            "lines": ["No epochs found where both constituent strategies also traded."],
        }

    overall_pct = 100.0 * total_inconsistent / total_checked

    if overall_pct < 5.0:
        status = STATUS_PASS
    elif overall_pct > 15.0:
        status = STATUS_FAIL
    else:
        status = STATUS_WARN

    lines = [
        f"Epochs checked: {total_checked}",
        f"Inconsistent epochs: {total_inconsistent} ({overall_pct:.1f}%)",
    ]
    for combined, r in results_by_combined.items():
        short_name = combined.replace("combined_", "")
        lines.append(
            f"  {short_name}: {r['inconsistent']}/{r['checked']} inconsistent "
            f"({r['inconsistent_pct']:.1f}%)"
        )
    if inconsistent_examples:
        lines.append("Examples of inconsistency:")
        for ex in inconsistent_examples:
            vals = ", ".join(f"{k}={v:.1%}" for k, v in ex["constituents"].items())
            lines.append(f"  epoch {ex['epoch']}: {vals}")

    return {
        "status": status,
        "details": {
            "total_checked": total_checked,
            "total_inconsistent": total_inconsistent,
            "inconsistent_pct": round(overall_pct, 2),
            "by_combined": results_by_combined,
            "inconsistent_examples": inconsistent_examples,
        },
        "lines": lines,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Check 4: Strategy Performance
# ──────────────────────────────────────────────────────────────────────────────

def check_strategy_performance(all_trades: dict[str, list]) -> dict:
    """Win rate, PnL, edge accuracy per strategy."""
    results = {}
    worst_wr = 1.0
    any_warn = False
    any_fail = False

    for strategy, trades in all_trades.items():
        resolved = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]
        n = len(resolved)
        if n == 0:
            results[strategy] = {"status": STATUS_NA, "trades": 0}
            continue

        wins = sum(1 for t in resolved if t.get("outcome") == "WIN")
        wr = wins / n
        pnl = sum(t.get("pnl_usdc", 0.0) for t in resolved)

        ci_lo, ci_hi = wilson_ci(wins, n)

        # Edge accuracy: sort by edge_at_entry, check WR quartiles
        edge_trades = sorted(resolved, key=lambda t: t.get("edge_at_entry", 0.0))
        edge_bins = {}
        quartile_size = max(1, n // 4)
        for i, t in enumerate(edge_trades):
            bin_idx = min(i // quartile_size, 3)
            if bin_idx not in edge_bins:
                edge_bins[bin_idx] = {"wins": 0, "total": 0, "avg_edge": 0.0, "edges": []}
            edge_bins[bin_idx]["total"] += 1
            if t.get("outcome") == "WIN":
                edge_bins[bin_idx]["wins"] += 1
            edge_bins[bin_idx]["edges"].append(t.get("edge_at_entry", 0.0))

        edge_correlation = []
        for idx in sorted(edge_bins.keys()):
            b = edge_bins[idx]
            avg_e = safe_mean(b["edges"])
            bwr = b["wins"] / b["total"] if b["total"] > 0 else 0.0
            edge_correlation.append({"avg_edge": round(avg_e, 4), "win_rate": round(bwr, 4), "count": b["total"]})

        # Check if higher edge → higher WR (monotone check on 4 bins)
        wrs = [ec["win_rate"] for ec in edge_correlation]
        edge_monotone = all(wrs[i] <= wrs[i+1] for i in range(len(wrs)-1)) if len(wrs) > 1 else None

        if n >= 50:
            if wr < 0.45:
                strat_status = STATUS_FAIL
                any_fail = True
            elif wr < 0.48:
                strat_status = STATUS_WARN
                any_warn = True
            else:
                strat_status = STATUS_PASS
        elif n >= 20:
            # Tentative: not flagged as fail but show warning if very low WR
            if wr < 0.40:
                strat_status = STATUS_WARN
                any_warn = True
            else:
                strat_status = STATUS_PASS
        else:
            strat_status = STATUS_NA  # not enough data

        worst_wr = min(worst_wr, wr)

        results[strategy] = {
            "status": strat_status,
            "trades": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": round(wr, 4),
            "win_rate_pct": round(wr * 100, 2),
            "ci_lo": round(ci_lo * 100, 1),
            "ci_hi": round(ci_hi * 100, 1),
            "pnl_usdc": round(pnl, 2),
            "edge_correlation": edge_correlation,
            "edge_monotone": edge_monotone,
        }

    # Overall status
    if any_fail:
        overall_status = STATUS_FAIL
    elif any_warn:
        overall_status = STATUS_WARN
    else:
        # Check if any strategy has enough trades for a proper assessment
        has_assessed = any(
            r.get("trades", 0) >= 20 and r.get("status") in (STATUS_PASS, STATUS_WARN, STATUS_FAIL)
            for r in results.values()
        )
        has_data = any(r.get("trades", 0) > 0 for r in results.values())
        if not has_data:
            overall_status = STATUS_NA
        elif not has_assessed:
            overall_status = STATUS_PASS  # all strategies have insufficient data — not alarming
        else:
            overall_status = STATUS_PASS

    lines = []
    for strategy, r in sorted(results.items()):
        if r.get("trades", 0) == 0:
            lines.append(f"  ➖ {strategy}: no resolved trades")
            continue
        status_icon = STATUS_ICONS.get(r["status"], "  ")
        ci_str = f" (CI: {r['ci_lo']:.1f}%-{r['ci_hi']:.1f}%)" if r["trades"] >= 20 else ""
        sample_note = " [small sample]" if r["trades"] < 20 else ""
        lines.append(
            f"  {status_icon} {strategy}: WR={r['win_rate_pct']:.1f}%{ci_str} | "
            f"PnL=${r['pnl_usdc']:+.2f} | n={r['trades']}{sample_note}"
        )
        if r.get("edge_monotone") is False and r["trades"] >= 10:
            lines.append(f"       ⚠️  Edge accuracy: higher edge does NOT correlate with more wins")

    return {
        "status": overall_status,
        "details": {"by_strategy": results},
        "lines": lines if lines else ["No resolved trades."],
    }

# ──────────────────────────────────────────────────────────────────────────────
# Check 5: System Health
# ──────────────────────────────────────────────────────────────────────────────

def check_system_health(all_trades: dict[str, list], all_log_lines: list[str]) -> dict:
    """Parse logs for errors, prefetch/RPC failures, missing data."""
    error_count = 0
    warning_count = 0
    prefetch_fails = 0
    rpc_fails = 0
    total_log_lines = len(all_log_lines)

    for line in all_log_lines:
        upper = line.upper()
        if "[ERROR]" in line or "ERROR" in upper and "traceback" not in upper.lower():
            error_count += 1
        if "[WARNING]" in line:
            warning_count += 1
        if re.search(r"prefetch", line, re.IGNORECASE) and re.search(r"fail|error|timeout", line, re.IGNORECASE):
            prefetch_fails += 1
        if re.search(r"rpc.*(fail|error|timeout)|web3.*error|bsc.*error|connection.*error", line, re.IGNORECASE):
            rpc_fails += 1

    # Missing data: trades with bull_pct=0 AND bear_pct=0
    missing_data = 0
    total_all_trades = 0
    for trades in all_trades.values():
        for t in trades:
            total_all_trades += 1
            bp = t.get("bull_pct", 0.0) or 0.0
            bep = t.get("bear_pct", 0.0) or 0.0
            if bp == 0.0 and bep == 0.0:
                missing_data += 1

    error_rate_pct = 100.0 * error_count / total_log_lines if total_log_lines > 0 else 0.0

    if error_rate_pct < 2.0:
        status = STATUS_PASS
    elif error_rate_pct > 10.0:
        status = STATUS_FAIL
    else:
        status = STATUS_WARN

    lines = [
        f"Log lines analyzed: {total_log_lines:,}",
        f"ERROR lines: {error_count} ({error_rate_pct:.2f}%)",
        f"WARNING lines: {warning_count}",
        f"Prefetch failures: {prefetch_fails}",
        f"RPC failures: {rpc_fails}",
        f"Trades with missing pool data (bull=0 & bear=0): {missing_data}/{total_all_trades}",
    ]

    return {
        "status": status,
        "details": {
            "total_log_lines": total_log_lines,
            "error_count": error_count,
            "error_rate_pct": round(error_rate_pct, 2),
            "warning_count": warning_count,
            "prefetch_fails": prefetch_fails,
            "rpc_fails": rpc_fails,
            "missing_pool_data_trades": missing_data,
            "total_trades": total_all_trades,
        },
        "lines": lines,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Check 6: Data Completeness
# ──────────────────────────────────────────────────────────────────────────────

def check_data_completeness(all_trades: dict[str, list]) -> dict:
    """Check final_bull/bear_pct, bnb prices, PENDING trades."""
    total = 0
    has_final_pool = 0
    has_bnb_prices = 0
    pending_old = []
    now_ts = __import__("time").time()

    for strategy, trades in all_trades.items():
        for t in trades:
            total += 1
            fbp = t.get("final_bull_pct", 0.0) or 0.0
            fbrp = t.get("final_bear_pct", 0.0) or 0.0
            if fbp != 0.0 or fbrp != 0.0:
                has_final_pool += 1

            bo = t.get("bnb_open", 0.0) or 0.0
            bc = t.get("bnb_close", 0.0) or 0.0
            if bo != 0.0 and bc != 0.0:
                has_bnb_prices += 1

            # PENDING: no outcome set OR outcome == "PENDING"
            outcome = t.get("outcome", "")
            tex = t.get("timestamp_exit")
            if not outcome or outcome == "PENDING":
                entry_ts = t.get("timestamp_entry", 0)
                age_minutes = (now_ts - entry_ts) / 60 if entry_ts else 999
                if age_minutes > 10:
                    pending_old.append({
                        "trade_id": t.get("trade_id"),
                        "strategy": strategy,
                        "epoch": t.get("epoch"),
                        "age_minutes": round(age_minutes, 1),
                    })

    if total == 0:
        return {
            "status": STATUS_NA,
            "details": {},
            "lines": ["No trades found."],
        }

    final_pool_pct = 100.0 * has_final_pool / total
    bnb_prices_pct = 100.0 * has_bnb_prices / total

    min_completeness = min(final_pool_pct, bnb_prices_pct)
    if min_completeness > 95.0 and not pending_old:
        status = STATUS_PASS
    elif min_completeness < 80.0 or len(pending_old) > 5:
        status = STATUS_FAIL
    else:
        status = STATUS_WARN

    lines = [
        f"Trades with final pool data: {has_final_pool}/{total} ({final_pool_pct:.1f}%)",
        f"Trades with BNB open/close: {has_bnb_prices}/{total} ({bnb_prices_pct:.1f}%)",
        f"PENDING trades older than 10min: {len(pending_old)}",
    ]
    for p in pending_old[:5]:
        lines.append(f"  ⚠️  {p['trade_id']} | epoch {p['epoch']} | {p['age_minutes']:.0f}min old")
    if len(pending_old) > 5:
        lines.append(f"  ... and {len(pending_old) - 5} more")

    return {
        "status": status,
        "details": {
            "total": total,
            "has_final_pool": has_final_pool,
            "final_pool_pct": round(final_pool_pct, 2),
            "has_bnb_prices": has_bnb_prices,
            "bnb_prices_pct": round(bnb_prices_pct, 2),
            "pending_old": pending_old,
        },
        "lines": lines,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Output: Rich / plain text
# ──────────────────────────────────────────────────────────────────────────────

WIDTH = 58

def status_badge(status: str) -> str:
    return f"[{STATUS_ICONS.get(status, '')} {status}]"

def print_header(mode: str, from_epoch: int | None, strategy_filter: str | None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    epoch_str = f"  •  From epoch: {from_epoch}" if from_epoch else ""
    strat_str = f"  •  Strategy: {strategy_filter}" if strategy_filter else ""
    subtitle = f"Mode: {mode}{epoch_str}{strat_str}"

    if RICH_AVAILABLE:
        console.print()
        console.print(f"╔{'═' * (WIDTH - 2)}╗")
        title = f"BNB-UPDOWN HEALTH CHECK  •  {now}"
        console.print(f"║  {title:<{WIDTH - 4}}║")
        console.print(f"║  {subtitle:<{WIDTH - 4}}║")
        console.print(f"╠{'═' * (WIDTH - 2)}╣")
        console.print()
    else:
        print()
        print(f"╔{'═' * (WIDTH - 2)}╗")
        print(f"║  BNB-UPDOWN HEALTH CHECK  •  {now:<{WIDTH - 33}}║")
        print(f"║  {subtitle:<{WIDTH - 4}}║")
        print(f"╠{'═' * (WIDTH - 2)}╣")
        print()

def color_status(status: str) -> str:
    """Return ANSI colored status string (fallback)."""
    colors = {STATUS_PASS: "\033[92m", STATUS_WARN: "\033[93m", STATUS_FAIL: "\033[91m", STATUS_NA: "\033[2m"}
    reset = "\033[0m"
    return f"{colors.get(status, '')}{status}{reset}"

def print_check(title: str, result: dict, warnings_only: bool = False):
    status = result["status"]
    lines = result.get("lines", [])

    if warnings_only and status == STATUS_PASS:
        return

    if RICH_AVAILABLE:
        color = STATUS_COLORS.get(status, "white")
        icon = STATUS_ICONS.get(status, "")
        header = f"📊 {title}"
        badge = f"[{color}][{icon} {status}][/{color}]"
        console.print(f"{header:<44}{badge}")
        for line in lines:
            console.print(f"  {line}")
        console.print()
    else:
        print(f"📊 {title:<40} [{color_status(status)}]")
        for line in lines:
            print(f"  {line}")
        print()

def print_footer(results: dict):
    counts = {STATUS_PASS: 0, STATUS_WARN: 0, STATUS_FAIL: 0, STATUS_NA: 0}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    summary = (
        f"{counts[STATUS_PASS]} PASS  •  "
        f"{counts[STATUS_WARN]} WARN  •  "
        f"{counts[STATUS_FAIL]} FAIL"
    )

    if RICH_AVAILABLE:
        console.print(f"{'═' * WIDTH}")
        if counts[STATUS_FAIL] > 0:
            color = "red"
        elif counts[STATUS_WARN] > 0:
            color = "yellow"
        else:
            color = "green"
        console.print(f"OVERALL: [{color}]{summary}[/{color}]")
        console.print(f"{'═' * WIDTH}")
        console.print()
    else:
        print(f"{'═' * WIDTH}")
        print(f"OVERALL: {summary}")
        print(f"{'═' * WIDTH}")
        print()

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BNB-UPDOWN Health Check — analyze trading quality",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mode", required=True, choices=["paper", "live"],
                        help="Trading mode to analyze")
    parser.add_argument("--strategy", default=None,
                        help="Analyze only this strategy (default: all)")
    parser.add_argument("--from-epoch", type=int, default=None,
                        help="Only include trades from this epoch onwards")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON (no pretty printing)")
    parser.add_argument("--warnings-only", action="store_true",
                        help="Only show WARN/FAIL checks (for cron monitoring)")
    args = parser.parse_args()

    mode = args.mode
    strategy_filter = args.strategy
    from_epoch = args.from_epoch

    # Discover strategies
    all_strategy_names = list_strategies(mode)
    if not all_strategy_names:
        print(f"No strategies found in logs/{mode}/", file=sys.stderr)
        sys.exit(1)

    # Filter if requested
    if strategy_filter:
        if strategy_filter not in all_strategy_names:
            print(f"Strategy '{strategy_filter}' not found in logs/{mode}/", file=sys.stderr)
            print(f"Available: {', '.join(all_strategy_names)}", file=sys.stderr)
            sys.exit(1)
        strategies_to_analyze = [strategy_filter]
    else:
        strategies_to_analyze = all_strategy_names

    # Load all trades
    all_trades: dict[str, list] = {}
    for s in strategies_to_analyze:
        trades = load_trades(mode, s, from_epoch)
        if trades:
            all_trades[s] = trades

    # Load all log lines
    all_log_lines: list[str] = []
    for s in strategies_to_analyze:
        all_log_lines.extend(parse_run_logs(mode, s))
    # Also top-level mode logs
    all_log_lines.extend(parse_run_logs(mode, None))

    # Run all checks
    checks = {}
    checks["timing"]       = check_timing_quality(all_trades, all_log_lines)
    checks["pool_drift"]   = check_pool_drift(all_trades)
    checks["consistency"]  = check_consistency(all_trades, all_strategy_names)
    checks["performance"]  = check_strategy_performance(all_trades)
    checks["system"]       = check_system_health(all_trades, all_log_lines)
    checks["completeness"] = check_data_completeness(all_trades)

    # JSON output
    if args.json:
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "strategy_filter": strategy_filter,
            "from_epoch": from_epoch,
            "strategies_analyzed": list(all_trades.keys()),
            "total_trades": sum(len(t) for t in all_trades.values()),
            "checks": {k: {"status": v["status"], "details": v["details"]} for k, v in checks.items()},
            "summary": {
                "pass": sum(1 for v in checks.values() if v["status"] == STATUS_PASS),
                "warn": sum(1 for v in checks.values() if v["status"] == STATUS_WARN),
                "fail": sum(1 for v in checks.values() if v["status"] == STATUS_FAIL),
            },
        }
        print(json.dumps(output, indent=2, default=str))
        return

    # Pretty output
    print_header(mode, from_epoch, strategy_filter)

    check_labels = {
        "timing":       "TIMING QUALITY",
        "pool_drift":   "POOL DRIFT",
        "consistency":  "CONSISTENCY",
        "performance":  "STRATEGY PERFORMANCE",
        "system":       "SYSTEM HEALTH",
        "completeness": "DATA COMPLETENESS",
    }

    for key, label in check_labels.items():
        print_check(label, checks[key], warnings_only=args.warnings_only)

    if not args.warnings_only:
        print_footer(checks)
    else:
        # Still show summary
        counts = {STATUS_PASS: 0, STATUS_WARN: 0, STATUS_FAIL: 0}
        for r in checks.values():
            if r["status"] in counts:
                counts[r["status"]] += 1
        summary = f"{counts[STATUS_PASS]} PASS • {counts[STATUS_WARN]} WARN • {counts[STATUS_FAIL]} FAIL"
        if RICH_AVAILABLE:
            console.print(f"{'─' * WIDTH}")
            console.print(f"Summary: {summary}")
        else:
            print(f"{'─' * WIDTH}")
            print(f"Summary: {summary}")

    # Exit code: non-zero on FAIL for cron/CI use
    has_fail = any(v["status"] == STATUS_FAIL for v in checks.values())
    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    main()
