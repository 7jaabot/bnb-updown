"""
Purge trades recorded before the last major logic change for each strategy.
Rewrites JSON + CSV in-place. Only removes; never creates or moves files.
"""
import csv
import glob
import json
import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Cutoffs: unix timestamp (UTC) — keep trades with timestamp_entry >= cutoff
# Derived from git commit timestamps of last major signal/edge logic change.
# ---------------------------------------------------------------------------

# Individual strategies (folder name → cutoff)
INDIVIDUAL_CUTOFFS = {
    # Group A: expand edge formulas — 2026-03-31 00:07:42 +0200 → UTC 22:07:42 Mar 30
    "fear_greed_micro":    datetime(2026, 3, 30, 22,  7, 42, tzinfo=timezone.utc).timestamp(),
    "follow_crowd":        datetime(2026, 3, 30, 22,  7, 42, tzinfo=timezone.utc).timestamp(),
    "market_regime":       datetime(2026, 3, 30, 22,  7, 42, tzinfo=timezone.utc).timestamp(),
    "order_flow":          datetime(2026, 3, 30, 22,  7, 42, tzinfo=timezone.utc).timestamp(),
    "rsi_reversal":        datetime(2026, 3, 30, 22,  7, 42, tzinfo=timezone.utc).timestamp(),

    # Group B: remove scaling/clamp constraints — 2026-03-31 07:03:35 +0200 → UTC 05:03:35 Mar 31
    "mean_reversion":      datetime(2026, 3, 31,  5,  3, 35, tzinfo=timezone.utc).timestamp(),
    "pool_contrarian":     datetime(2026, 3, 31,  5,  3, 35, tzinfo=timezone.utc).timestamp(),

    # Group B2: remove p_up/p_win clamps — 2026-03-31 06:50:19 +0200 → UTC 04:50:19 Mar 31
    "whale_signal":        datetime(2026, 3, 31,  4, 50, 19, tzinfo=timezone.utc).timestamp(),

    # Group C: remove pre-filter / remap factor — 2026-04-01 09:53:32 +0200 → UTC 07:53:32
    "correlation_arbitrage": datetime(2026, 4,  1,  7, 53, 32, tzinfo=timezone.utc).timestamp(),
    # orderbook already cleaned separately

    # Group D: remove pre-filters — 2026-04-01 23:44:46 +0200 → UTC 21:44:46
    "liquidation_reversal": datetime(2026, 4,  1, 21, 44, 46, tzinfo=timezone.utc).timestamp(),
    "volume_breakout":      datetime(2026, 4,  1, 21, 44, 46, tzinfo=timezone.utc).timestamp(),
}

# Combined strategies: prefix of folder name (without trailing _<pid>) → cutoff
# Cutoff = max(component cutoffs)
COMBINED_CUTOFFS = {
    # Paper combined
    "combined_fear_greed_micro_pool_contrarian":        datetime(2026, 3, 31,  5,  3, 35, tzinfo=timezone.utc).timestamp(),
    "combined_funding_rate_order_flow_pool_contrarian": datetime(2026, 3, 31,  5,  3, 35, tzinfo=timezone.utc).timestamp(),
    "combined_gbm_orderbook":                           datetime(2026, 4,  1,  7, 53, 32, tzinfo=timezone.utc).timestamp(),
    # Live combined
    "combined_fear_greed_micro_orderbook":              datetime(2026, 4,  1,  7, 53, 32, tzinfo=timezone.utc).timestamp(),
    "combined_funding_rate_gbm_order_flow_volume_breakout": datetime(2026, 4,  1, 21, 44, 46, tzinfo=timezone.utc).timestamp(),
    "combined_funding_rate_orderbook":                  datetime(2026, 4,  1,  7, 53, 32, tzinfo=timezone.utc).timestamp(),
    "combined_funding_rate_orderbook_rsi_reversal":     datetime(2026, 4,  1,  7, 53, 32, tzinfo=timezone.utc).timestamp(),
    "combined_orderbook_pool_contrarian_rsi_reversal":  datetime(2026, 4,  1,  7, 53, 32, tzinfo=timezone.utc).timestamp(),
}

SKIP_FILENAMES = {"all_rounds.json", "pool_snapshots.json"}


def ts_to_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def recompute_metrics(trades, is_live_combined=False):
    wins = sum(1 for t in trades if t.get("outcome") == "WIN")
    losses = sum(1 for t in trades if t.get("outcome") == "LOSS")
    pending = sum(1 for t in trades if t.get("outcome") not in ("WIN", "LOSS"))
    total_pnl = round(sum(t.get("pnl_usdc", 0) for t in trades), 4)
    total_wagered = sum(t.get("position_size_usdc", 0) for t in trades)
    win_rate = wins / len(trades) if trades else 0
    roi = total_pnl / total_wagered if total_wagered else 0
    avg_edge = sum(t.get("edge_at_entry", 0) for t in trades) / len(trades) if trades else 0

    metrics = {
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "roi": roi,
        "total_wagered": total_wagered,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "avg_edge": avg_edge,
    }

    if is_live_combined:
        metrics["daily_pnl"] = total_pnl
    else:
        # Paper: reset bankroll to 1000 + accumulated pnl
        metrics["bankroll"] = round(1000 + total_pnl, 4)

    return metrics


def rebuild_csv(json_path, trades):
    csv_path = json_path.replace(".json", ".csv")
    if not os.path.exists(csv_path):
        return  # no paired CSV

    # Read header from existing CSV to preserve column order exactly
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames

    if not fieldnames:
        return

    rows = []
    for t in trades:
        row = {
            "trade_id":           t.get("trade_id", ""),
            "epoch":              t.get("epoch", ""),
            "timestamp_entry":    t.get("timestamp_entry", ""),
            "time_entry":         ts_to_str(t["timestamp_entry"]) if "timestamp_entry" in t else "",
            "timestamp_exit":     t.get("timestamp_exit", ""),
            "time_exit":          ts_to_str(t["timestamp_exit"]) if "timestamp_exit" in t else "",
            "side":               t.get("side", ""),
            "side_label":         "UP" if t.get("side") == "YES" else "DOWN",
            "edge_at_entry":      t.get("edge_at_entry", ""),
            "p_up_at_entry":      t.get("p_up_at_entry", ""),
            "kelly_fraction":     t.get("kelly_fraction", 0.0),
            "position_size_usdc": t.get("position_size_usdc", ""),
            "bet_bnb":            t.get("bet_bnb", ""),
            "bnb_price_at_entry": t.get("bnb_price_at_entry", ""),
            "bull_pct":           t.get("bull_pct", ""),
            "bear_pct":           t.get("bear_pct", ""),
            "final_bull_pct":     t.get("final_bull_pct", ""),
            "final_bear_pct":     t.get("final_bear_pct", ""),
            "final_total_bnb":    t.get("final_total_bnb", ""),
            "pool_drift_pct":     t.get("pool_drift_pct", ""),
            "window_end_ts":      t.get("window_end_ts", ""),
            "bnb_open":           t.get("bnb_open", ""),
            "bnb_close":          t.get("bnb_close", ""),
            "outcome":            t.get("outcome", ""),
            "pnl_usdc":           t.get("pnl_usdc", ""),
            "payout_per_share":   t.get("payout_per_share", ""),
            "is_mock":            t.get("is_mock", False),
            # Live-only fields (present only if in fieldnames)
            "tx_hash":            t.get("tx_hash", ""),
            "tx_status":          t.get("tx_status", ""),
            "claim_tx_hash":      t.get("claim_tx_hash", ""),
        }
        rows.append({k: row[k] for k in fieldnames if k in row})

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def get_cutoff(json_path):
    """Return cutoff timestamp for a given json path, or None if no purge needed."""
    folder = os.path.basename(os.path.dirname(json_path))

    # Skip special files
    filename = os.path.basename(json_path)
    if filename in SKIP_FILENAMES:
        return None
    if filename.startswith("wallet-"):
        return None

    # Individual strategy match (exact folder name)
    if folder in INDIVIDUAL_CUTOFFS:
        return INDIVIDUAL_CUTOFFS[folder]

    # Combined strategy match (prefix, strip trailing _<pid>)
    if folder.startswith("combined_"):
        # Strip trailing numeric pid: e.g. combined_foo_bar_12345 → combined_foo_bar
        parts = folder.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            prefix = parts[0]
            if prefix in COMBINED_CUTOFFS:
                return COMBINED_CUTOFFS[prefix]

    return None


def purge_file(json_path, cutoff, is_live_combined):
    with open(json_path) as f:
        data = json.load(f)

    trades = data.get("trades", [])
    if not trades:
        return 0, 0

    kept = [t for t in trades if t.get("timestamp_entry", 0) >= cutoff]
    removed = len(trades) - len(kept)

    if removed == 0:
        return 0, len(kept)

    data["trades"] = kept
    data["metadata"]["total_trades"] = len(kept)
    data["metadata"]["last_updated_iso"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["metrics"] = recompute_metrics(kept, is_live_combined=is_live_combined)

    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    rebuild_csv(json_path, kept)
    return removed, len(kept)


def main():
    base = os.path.join(os.path.dirname(__file__), "..", "logs")
    all_json = sorted(glob.glob(os.path.join(base, "**", "*.json"), recursive=True))

    total_removed = 0
    touched = 0

    for json_path in all_json:
        cutoff = get_cutoff(json_path)
        if cutoff is None:
            continue

        is_live = os.sep + "live" + os.sep in json_path or "/live/" in json_path
        is_combined = "combined_" in os.path.basename(os.path.dirname(json_path))
        is_live_combined = is_live and is_combined

        removed, kept = purge_file(json_path, cutoff, is_live_combined)
        rel = os.path.relpath(json_path, start=os.path.join(os.path.dirname(__file__), ".."))

        if removed > 0:
            cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"  PURGED  {rel}: removed {removed}, kept {kept}  (cutoff {cutoff_dt})")
            total_removed += removed
            touched += 1
        else:
            print(f"  clean   {rel}: {kept} trades already past cutoff")

    print(f"\nDone. {touched} files modified, {total_removed} trades removed total.")


if __name__ == "__main__":
    main()
