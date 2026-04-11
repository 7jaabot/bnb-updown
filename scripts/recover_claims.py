"""
recover_claims.py — One-shot script to recover unclaimed PancakeSwap Prediction wins.

Reads the coordination JSON, checks each epoch on-chain for claimability,
and batch-claims all recoverable winnings.

Usage:
    python scripts/recover_claims.py              # dry-run (default)
    python scripts/recover_claims.py --execute    # actually send claim TX
"""

import json
import os
import sys
import time
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
COORDINATION_DIR = ROOT / "logs" / "live" / ".coordination"
PANCAKE_CONTRACT = "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA"

PANCAKE_ABI = [
    {
        "name": "claim",
        "type": "function",
        "inputs": [{"name": "epochs", "type": "uint256[]"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "claimable",
        "type": "function",
        "inputs": [
            {"name": "epoch", "type": "uint256"},
            {"name": "user", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "name": "refundable",
        "type": "function",
        "inputs": [
            {"name": "epoch", "type": "uint256"},
            {"name": "user", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
]

BSC_RPC_URLS = [
    "https://bsc-dataseed.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed2.defibit.io/",
    "https://1rpc.io/bnb",
]


def connect_bsc():
    """Connect to BSC and return (w3, account, wallet_address, contract)."""
    from dotenv import load_dotenv

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware

    private_key = os.environ.get("BSC_PRIVATE_KEY", "").strip()
    wallet_addr = os.environ.get("BSC_WALLET_ADDRESS", "").strip()

    if not private_key:
        print("ERROR: BSC_PRIVATE_KEY not set in .env")
        sys.exit(1)
    if not wallet_addr:
        print("ERROR: BSC_WALLET_ADDRESS not set in .env")
        sys.exit(1)

    w3 = None
    for url in BSC_RPC_URLS:
        try:
            candidate = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
            candidate.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            _ = candidate.eth.block_number
            w3 = candidate
            print(f"Connected to BSC via {url}")
            break
        except Exception:
            continue

    if w3 is None:
        print("ERROR: Cannot connect to BSC")
        sys.exit(1)

    wallet = w3.to_checksum_address(wallet_addr)
    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(
        address=w3.to_checksum_address(PANCAKE_CONTRACT), abi=PANCAKE_ABI
    )

    balance_bnb = w3.eth.get_balance(wallet) / 1e18
    print(f"Wallet: {wallet}")
    print(f"Balance: {balance_bnb:.6f} BNB")

    return w3, account, wallet, contract


def load_coordination_epochs():
    """Load all epochs from coordination JSON files."""
    all_epochs = {}
    for f in COORDINATION_DIR.glob("wallet-*.json"):
        if f.name.endswith(".pid") or f.name.endswith(".lock"):
            continue
        try:
            data = json.loads(f.read_text())
            epochs = data.get("epochs", {})
            for epoch_str, info in epochs.items():
                all_epochs[int(epoch_str)] = info
        except Exception as e:
            print(f"WARNING: Failed to read {f}: {e}")
    return all_epochs


def main():
    execute = "--execute" in sys.argv

    print("=" * 60)
    print("  PancakeSwap Prediction — Unclaimed Wins Recovery")
    print(f"  Mode: {'EXECUTE' if execute else 'DRY-RUN'}")
    print("=" * 60)
    print()

    # Load coordination state
    epochs = load_coordination_epochs()
    print(f"Found {len(epochs)} epochs in coordination state")
    print()

    # Connect to BSC
    w3, account, wallet, contract = connect_bsc()
    print()

    # Check each epoch
    claimable_epochs = []
    refundable_epochs = []
    already_claimed = []
    not_claimable = []

    print("Checking epochs on-chain...")
    for epoch in sorted(epochs.keys()):
        info = epochs[epoch]
        try:
            is_claimable = contract.functions.claimable(epoch, wallet).call()
            is_refundable = contract.functions.refundable(epoch, wallet).call()

            side = info.get("side", "?")
            pid = info.get("instance_id", "?")
            bet = info.get("bet_bnb", 0)

            if is_claimable:
                claimable_epochs.append(epoch)
                print(f"  epoch {epoch}: CLAIMABLE  (side={side}, bet={bet:.6f} BNB, pid={pid})")
            elif is_refundable:
                refundable_epochs.append(epoch)
                print(f"  epoch {epoch}: REFUNDABLE (side={side}, bet={bet:.6f} BNB, pid={pid})")
            else:
                not_claimable.append(epoch)
        except Exception as e:
            # claimable() reverts if already claimed
            already_claimed.append(epoch)

    print()
    print(f"Results:")
    print(f"  Claimable:     {len(claimable_epochs)}")
    print(f"  Refundable:    {len(refundable_epochs)}")
    print(f"  Not claimable: {len(not_claimable)}")
    print(f"  Already claimed/reverted: {len(already_claimed)}")
    print()

    all_to_claim = claimable_epochs + refundable_epochs
    if not all_to_claim:
        print("Nothing to claim. Exiting.")
        return

    # Estimate total recoverable (bet amount * payout ratio for wins, or just bet for refunds)
    total_bet = sum(epochs[e].get("bet_bnb", 0) for e in all_to_claim)
    print(f"Total bet amount across {len(all_to_claim)} claimable epochs: {total_bet:.6f} BNB")
    print(f"(Actual payout depends on pool ratios — will be >= bet amount for wins)")
    print()

    if not execute:
        print("DRY-RUN mode — no transaction sent.")
        print("Run with --execute to claim.")
        return

    # Send claim TX
    print(f"Claiming {len(all_to_claim)} epochs...")
    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(wallet)

    fn = contract.functions.claim(all_to_claim)
    try:
        gas_estimate = fn.estimate_gas({"from": wallet})
        gas_limit = int(gas_estimate * 1.3)
    except Exception as e:
        print(f"Gas estimation failed: {e}")
        gas_limit = 300_000

    tx = fn.build_transaction({
        "from": wallet,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": 56,
    })

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash_hex = tx_hash.hex()
    print(f"TX sent: {tx_hash_hex}")

    # Wait for confirmation
    print("Waiting for confirmation...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status == 1:
        new_balance = w3.eth.get_balance(wallet) / 1e18
        print(f"CLAIMED SUCCESSFULLY — block {receipt.blockNumber}")
        print(f"New balance: {new_balance:.6f} BNB")
        print(f"Gas used: {receipt.gasUsed}")
    else:
        print(f"CLAIM FAILED — tx: {tx_hash_hex}")
        print("Check the transaction on BscScan for details.")


if __name__ == "__main__":
    main()
