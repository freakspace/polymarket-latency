#!/usr/bin/env python3
"""
Wrap USDC.e into pUSD for a Polymarket Ravn Safe.

The Safe (`POLYMARKET_FUNDER`) holds the USDC.e. We sign two Safe transactions
with the owner EOA (`POLYMARKET_PRIVATE_KEY`) and execute them on Polygon:

  1. USDC.e.approve(Onramp, amount)
  2. Onramp.wrap(USDC.e, Safe, amount)

Both txs are submitted by the owner EOA calling `Safe.execTransaction(...)`,
so the EOA needs a small amount of MATIC for gas. The resulting pUSD lands
in the Safe, which is what the CLOB v2 collateral check expects.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from decimal import Decimal

from dotenv import load_dotenv
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address


ONRAMP = to_checksum_address("0x93070a847efEf7F70739046A929D47a521F5B8ee")
USDCE = to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
PUSD = to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
EXCHANGE_V2 = to_checksum_address("0xE111180000d2663C0091e4f400237545B87B996B")
NEG_RISK_ADAPTER = to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")
NEG_RISK_EXCHANGE_V2 = to_checksum_address("0xe2222d279d744050d28e00520010520000310F59")
V2_SPENDERS = (EXCHANGE_V2, NEG_RISK_ADAPTER, NEG_RISK_EXCHANGE_V2)
MAX_UINT256 = (1 << 256) - 1
ZERO = "0x" + "0" * 40
CHAIN_ID = 137
DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"


def allowance_of(url: str, token: str, owner: str, spender: str) -> int:
    data = encode_call(
        "allowance(address,address)", ["address", "address"], [owner, spender]
    )
    return int_call(url, token, data)


def rpc(url: str, method: str, params: list) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "curl/8.1"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    if "error" in payload:
        raise RuntimeError(f"RPC {method} error: {payload['error']}")
    return payload["result"]


def selector(sig: str) -> bytes:
    return keccak(sig.encode("ascii"))[:4]


def encode_call(sig: str, types: list[str], values: list) -> bytes:
    return selector(sig) + abi_encode(types, values)


def eth_call(url: str, to: str, data: bytes) -> bytes:
    result = rpc(url, "eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"])
    return bytes.fromhex(result[2:])


def int_call(url: str, to: str, data: bytes) -> int:
    return int.from_bytes(eth_call(url, to, data), "big")


def balance_of(url: str, token: str, owner: str) -> int:
    return int_call(url, token, encode_call("balanceOf(address)", ["address"], [owner]))


def matic_balance(url: str, who: str) -> int:
    return int(rpc(url, "eth_getBalance", [who, "latest"]), 16)


def safe_nonce(url: str, safe: str) -> int:
    return int_call(url, safe, encode_call("nonce()", [], []))


def safe_threshold(url: str, safe: str) -> int:
    return int_call(url, safe, encode_call("getThreshold()", [], []))


def safe_owners(url: str, safe: str) -> list[str]:
    raw = eth_call(url, safe, encode_call("getOwners()", [], []))
    count = int.from_bytes(raw[32:64], "big")
    out = []
    for i in range(count):
        word = raw[64 + i * 32 : 64 + (i + 1) * 32]
        out.append(to_checksum_address("0x" + word[-20:].hex()))
    return out


def safe_tx_hash(url: str, safe: str, to: str, value: int, data: bytes, nonce: int) -> bytes:
    """Ask the Safe to compute the EIP-712 tx hash for a simple zero-gas Safe tx."""
    call = encode_call(
        "getTransactionHash(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,uint256)",
        ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address", "address", "uint256"],
        [to, value, data, 0, 0, 0, 0, ZERO, ZERO, nonce],
    )
    return eth_call(url, safe, call)


def sign_safe_tx(pk: str, tx_hash: bytes) -> bytes:
    """Return r||s||v (65 bytes), signed directly over the Safe's EIP-712 tx hash."""
    acct = Account.from_key(pk)
    signed = acct.unsafe_sign_hash(tx_hash)
    return signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([signed.v])


def exec_transaction_data(to: str, value: int, data: bytes, signatures: bytes) -> bytes:
    return encode_call(
        "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)",
        ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address", "address", "bytes"],
        [to, value, data, 0, 0, 0, 0, ZERO, ZERO, signatures],
    )


def send_tx(url: str, pk: str, to: str, data: bytes, nonce: int) -> str:
    # Use the node's suggested gas price as a legacy tx — max_fee stays tight
    # which matters when the owner EOA is near-empty.
    gas_price = int(rpc(url, "eth_gasPrice", []), 16)
    tx = {
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "to": to,
        "data": "0x" + data.hex(),
        "value": 0,
        "gasPrice": gas_price,
    }
    est_req = {
        "from": Account.from_key(pk).address,
        "to": tx["to"],
        "data": tx["data"],
        "value": hex(tx["value"]),
    }
    try:
        est = int(rpc(url, "eth_estimateGas", [est_req]), 16)
    except Exception as exc:
        raise RuntimeError(f"gas estimate failed (tx would revert): {exc}")
    tx["gas"] = int(est * 1.2)
    signed = Account.sign_transaction(tx, pk)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    raw_hex = raw.hex() if isinstance(raw, bytes) else raw
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex
    return rpc(url, "eth_sendRawTransaction", [raw_hex])


def wait_for_receipt(url: str, tx_hash: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        receipt = rpc(url, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        time.sleep(2.0)
    raise TimeoutError(f"no receipt for {tx_hash} within {timeout}s")


def execute_safe_call(
    url: str,
    pk: str,
    safe: str,
    target: str,
    inner_data: bytes,
    eoa_nonce: int,
    label: str,
) -> tuple[str, int]:
    nonce = safe_nonce(url, safe)
    tx_hash = safe_tx_hash(url, safe, target, 0, inner_data, nonce)
    sig = sign_safe_tx(pk, tx_hash)
    outer = exec_transaction_data(target, 0, inner_data, sig)
    print(f"  [{label}] safe_nonce={nonce} safeTxHash=0x{tx_hash.hex()}")
    tx = send_tx(url, pk, safe, outer, eoa_nonce)
    print(f"  [{label}] sent tx: {tx}")
    receipt = wait_for_receipt(url, tx)
    status = int(receipt["status"], 16)
    if status != 1:
        raise RuntimeError(f"{label} reverted: {receipt}")
    block = int(receipt["blockNumber"], 16)
    gas_used = int(receipt["gasUsed"], 16)
    print(f"  [{label}] mined in block {block}, gas={gas_used}")
    return tx, eoa_nonce + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wrap USDC.e → pUSD into your Polymarket Ravn Safe."
    )
    parser.add_argument(
        "amount",
        nargs="?",
        help=(
            "Amount in USDC.e (e.g. '1.5'). Omit to wrap the Safe's full USDC.e "
            "balance minus a tiny dust buffer."
        ),
    )
    parser.add_argument(
        "--dust-keep-wei",
        type=int,
        default=1,
        help="When amount is omitted, leave this many micro-USDC.e behind (default: 1).",
    )
    parser.add_argument(
        "--rpc",
        default=os.environ.get("POLYGON_RPC_URL", DEFAULT_RPC),
        help=f"Polygon JSON-RPC URL. Default: env POLYGON_RPC_URL or {DEFAULT_RPC}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen and exit without signing or sending.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    pk = os.environ.get("POLYMARKET_PRIVATE_KEY") or os.environ.get("PK")
    safe_raw = os.environ.get("POLYMARKET_FUNDER")
    if not pk or not safe_raw:
        print("POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER must be set.", file=sys.stderr)
        return 2

    safe = to_checksum_address(safe_raw)
    owner = Account.from_key(pk).address
    rpc_url = args.rpc

    print(f"RPC: {rpc_url}")
    print(f"Safe: {safe}")
    print(f"Owner EOA: {owner}")

    owners = safe_owners(rpc_url, safe)
    threshold = safe_threshold(rpc_url, safe)
    print(f"Safe owners: {owners} threshold={threshold}")
    if to_checksum_address(owner) not in owners:
        print("Owner EOA is not an owner of this Safe; aborting.", file=sys.stderr)
        return 2
    if threshold != 1:
        print(f"Safe threshold is {threshold}; this helper assumes a 1-of-N Safe.", file=sys.stderr)
        return 2

    safe_usdce = balance_of(rpc_url, USDCE, safe)
    safe_pusd = balance_of(rpc_url, PUSD, safe)
    eoa_matic = matic_balance(rpc_url, owner)
    print(f"Before: Safe USDC.e={Decimal(safe_usdce)/Decimal(10**6)}  Safe pUSD={Decimal(safe_pusd)/Decimal(10**6)}  Owner MATIC={Decimal(eoa_matic)/Decimal(10**18)}")

    if args.amount:
        amount_units = int((Decimal(args.amount) * Decimal(10**6)).to_integral_value())
    else:
        amount_units = max(0, safe_usdce - args.dust_keep_wei)

    if amount_units > safe_usdce:
        print(f"Safe holds {safe_usdce} micro-USDC.e, requested {amount_units}; aborting.", file=sys.stderr)
        return 2
    skip_wrap = amount_units <= 0
    if skip_wrap:
        print("Skipping wrap (nothing meaningful to wrap); will still ensure CLOB v2 approvals.")
    min_pol = 5 * 10**15  # 0.005 POL
    if eoa_matic < min_pol:
        print(
            f"Owner POL balance is {Decimal(eoa_matic)/Decimal(10**18)}; need at least 0.005 POL for two Safe transactions.",
            file=sys.stderr,
        )
        if not args.dry_run:
            return 2

    print(f"Wrap plan: move {Decimal(amount_units)/Decimal(10**6)} USDC.e → pUSD into Safe {safe}")
    if args.dry_run:
        print("Dry run; no transactions sent.")
        return 0

    eoa_nonce = int(rpc(rpc_url, "eth_getTransactionCount", [owner, "pending"]), 16)

    if not skip_wrap:
        approve_inner = encode_call(
            "approve(address,uint256)", ["address", "uint256"], [ONRAMP, amount_units]
        )
        print("Step 1/2: Safe → USDC.e.approve(Onramp, amount)")
        _, eoa_nonce = execute_safe_call(
            rpc_url, pk, safe, USDCE, approve_inner, eoa_nonce, "approve"
        )

        wrap_inner = encode_call(
            "wrap(address,address,uint256)",
            ["address", "address", "uint256"],
            [USDCE, safe, amount_units],
        )
        print("Step 2/2: Safe → Onramp.wrap(USDC.e, Safe, amount)")
        _, eoa_nonce = execute_safe_call(
            rpc_url, pk, safe, ONRAMP, wrap_inner, eoa_nonce, "wrap"
        )

        safe_usdce_after = balance_of(rpc_url, USDCE, safe)
        safe_pusd_after = balance_of(rpc_url, PUSD, safe)
        print(f"After:  Safe USDC.e={Decimal(safe_usdce_after)/Decimal(10**6)}  Safe pUSD={Decimal(safe_pusd_after)/Decimal(10**6)}")

    # Approve the three CLOB v2 spenders to pull pUSD from the Safe.
    for i, spender in enumerate(V2_SPENDERS, start=1):
        current = allowance_of(rpc_url, PUSD, safe, spender)
        if current >= MAX_UINT256 // 2:
            print(f"Step extra {i}/{len(V2_SPENDERS)}: pUSD allowance for {spender} already set ({current}); skipping.")
            continue
        approve_pusd = encode_call(
            "approve(address,uint256)", ["address", "uint256"], [spender, MAX_UINT256]
        )
        print(f"Step extra {i}/{len(V2_SPENDERS)}: Safe → pUSD.approve({spender}, MAX)")
        _, eoa_nonce = execute_safe_call(
            rpc_url, pk, safe, PUSD, approve_pusd, eoa_nonce, f"approve-pusd-{i}"
        )

    print("Final allowances (pUSD):")
    for spender in V2_SPENDERS:
        a = allowance_of(rpc_url, PUSD, safe, spender)
        print(f"  {spender}: {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
