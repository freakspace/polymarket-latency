#!/usr/bin/env python3
"""
Burst-submit Polymarket CLOB v2 orders to probe duplicate handling and latency.

For each fanout in --counts, the script either:
1. Re-sends the exact same signed order N times (`exact-duplicate` mode), or
2. Signs N distinct orders sharing the same V2 timestamp (`shared-timestamp` mode).

Then it:
1. Sends them as N concurrent POST /order API calls.
2. Measures per-request latency and records the returned status/error.
3. Checks how many new open orders actually appeared on the book.
4. Optionally cancels those new orders to keep the market clean.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, deque
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dotenv import load_dotenv
from py_clob_client_v2 import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    BuilderConfig,
    ClobClient,
    OpenOrderParams,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
)


DEFAULT_HOST = "https://clob-v2.polymarket.com"
DEFAULT_CHAIN_ID = 80002
DEFAULT_SETTLE_SECONDS = 1.0
DEFAULT_OUTPUT_DIR = Path("recordings/order-burst")
BUY = "BUY"
SELL = "SELL"
EXACT_DUPLICATE = "exact-duplicate"
SHARED_TIMESTAMP = "shared-timestamp"

load_dotenv()


class SdkHttpErrorCapture(logging.Handler):
    def __init__(self, max_entries_per_thread: int = 20):
        super().__init__(level=logging.ERROR)
        self._lock = threading.Lock()
        self._seq = 0
        self._records_by_thread: dict[int, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_entries_per_thread)
        )

    def mark(self) -> int:
        with self._lock:
            return self._seq

    def latest_since(self, thread_id: int, seq: int) -> dict[str, Any] | None:
        with self._lock:
            records = self._records_by_thread.get(thread_id)
            if not records:
                return None
            for record in reversed(records):
                if record["seq"] > seq:
                    return dict(record)
            return None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        entry = {
            "seq": None,
            "thread_id": record.thread,
            "logger": record.name,
            "level": record.levelname,
            "message": message,
            "created": record.created,
        }
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            self._records_by_thread[record.thread].append(entry)


SDK_HTTP_ERROR_CAPTURE = SdkHttpErrorCapture()
SDK_HTTP_HELPERS_LOGGER = logging.getLogger("py_clob_client_v2.http_helpers.helpers")
if not any(handler is SDK_HTTP_ERROR_CAPTURE for handler in SDK_HTTP_HELPERS_LOGGER.handlers):
    SDK_HTTP_HELPERS_LOGGER.addHandler(SDK_HTTP_ERROR_CAPTURE)


def env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fire concurrent V2 testnet order submissions with a shared "
            "timestamp to test duplicate handling and latency."
        )
    )
    parser.add_argument(
        "--token-id",
        required=True,
        help="Outcome token ID to trade on clob-v2.",
    )
    parser.add_argument(
        "--side",
        choices=(BUY, SELL),
        default=BUY,
        help="Order side. Default: BUY.",
    )
    parser.add_argument(
        "--price",
        type=Decimal,
        help=(
            "Limit price to submit. If omitted, the script chooses a very "
            "passive default that should not cross the book."
        ),
    )
    parser.add_argument(
        "--size",
        type=Decimal,
        help="Order size in shares. Defaults to the market min order size.",
    )
    parser.add_argument(
        "--counts",
        default="1,2,5,10",
        help="Comma-separated fanouts to test. Default: 1,2,5,10.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=int(os.environ.get("REPEATS", "1")),
        help="How many full burst runs to execute. Default: 1.",
    )
    parser.add_argument(
        "--burst-mode",
        choices=(EXACT_DUPLICATE, SHARED_TIMESTAMP),
        default=os.environ.get("BURST_MODE", EXACT_DUPLICATE),
        help=(
            "Order construction mode. "
            f"`{EXACT_DUPLICATE}` re-sends the exact same signed order N times. "
            f"`{SHARED_TIMESTAMP}` signs N distinct orders with one shared timestamp. "
            f"Default: {EXACT_DUPLICATE}."
        ),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("CLOB_API_URL", DEFAULT_HOST),
        help=f"CLOB host. Default: {DEFAULT_HOST}.",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.environ.get("CHAIN_ID", DEFAULT_CHAIN_ID)),
        help=f"Chain ID. Default: {DEFAULT_CHAIN_ID} (Amoy testnet).",
    )
    parser.add_argument(
        "--post-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Submit orders as post-only. Default: enabled.",
    )
    parser.add_argument(
        "--cleanup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Cancel newly-created orders after each burst. Default: enabled.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=DEFAULT_SETTLE_SECONDS,
        help=(
            "Seconds to wait before checking open orders after a burst. "
            f"Default: {DEFAULT_SETTLE_SECONDS}."
        ),
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help=(
            "Path to write the full result JSON. "
            "Default: recordings/order-burst/<timestamp>/summary.json."
        ),
    )
    return parser.parse_args()


def parse_counts(raw: str) -> list[int]:
    counts: list[int] = []
    for part in raw.split(","):
        value = int(part.strip())
        if value <= 0:
            raise ValueError("all burst counts must be positive integers")
        counts.append(value)
    return counts


def require_positive_int(name: str, value: int) -> int:
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def decimal_to_str(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def require_any_env(*names: str) -> str:
    value = env_first(*names)
    if not value:
        joined = ", ".join(names)
        raise RuntimeError(f"missing required environment variable (one of: {joined})")
    return value


def env_truthy(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def maybe_load_creds_from_env() -> ApiCreds | None:
    if env_truthy("FORCE_DERIVE"):
        return None

    api_key = env_first("CLOB_API_KEY", "POLYMARKET_API_KEY")
    api_secret = env_first("CLOB_SECRET", "POLYMARKET_API_SECRET")
    api_passphrase = env_first(
        "CLOB_PASS_PHRASE",
        "CLOB_PASSPHRASE",
        "POLYMARKET_API_PASSPHRASE",
    )
    if api_key and api_secret and api_passphrase:
        return ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
    return None


def env_builder_code() -> str | None:
    return env_first(
        "BUILDER_CODE",
        "POLY_BUILDER_CODE",
        "POLYMARKET_BUILDER_CODE",
    )


def parse_signature_type(raw: str | None, *, default: int = 0) -> int:
    if raw is None:
        return default

    value = raw.strip()
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        normalized = value.upper()
        mapping = {
            "EOA": 0,
            "POLY_PROXY": 1,
            "PROXY": 1,
            "POLY_GNOSIS_SAFE": 2,
            "GNOSIS_SAFE": 2,
            "SAFE": 2,
        }
        return mapping.get(normalized, default)


def candidate_chain_ids(initial_chain_id: int) -> list[int]:
    chain_ids = [initial_chain_id]
    if initial_chain_id == 80002:
        chain_ids.append(137)
    elif initial_chain_id == 137:
        chain_ids.append(80002)
    return chain_ids


def maybe_builder_config() -> BuilderConfig | None:
    builder_code = env_builder_code()
    if not builder_code:
        return None
    return BuilderConfig(builder_code=builder_code)


def verify_builder_code(host: str, builder_code: str) -> None:
    import urllib.request

    url = f"{host.rstrip('/')}/fees/builder-fees/{builder_code}"
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket_order_burst"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(400)
        print(f"[builder] code {builder_code} recognized, fee rate payload: {body!r}")
    except Exception as exc:
        raise RuntimeError(
            f"builder_code {builder_code!r} was not recognized by {url} ({exc}). "
            "Use the bytes32 code Polymarket issued your builder, not the builder API-key UUID."
        )


def fetch_funder_kind(host: str, chain_id: int, funder: str) -> str:
    """Return 'eoa' | 'gnosis_safe' | 'poly_proxy' | 'unknown' by inspecting on-chain bytecode.

    We only try to classify on Polygon mainnet (chain 137); on other chains we return 'unknown'.
    """
    import json
    import urllib.request

    if chain_id != 137 or not funder:
        return "unknown"

    rpc_urls = [
        "https://polygon-bor-rpc.publicnode.com",
        "https://rpc.ankr.com/polygon",
    ]
    for rpc in rpc_urls:
        try:
            body = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "eth_getCode",
                "params": [funder, "latest"],
            }).encode()
            req = urllib.request.Request(
                rpc,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "curl/8.1"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = json.loads(resp.read()).get("result", "0x")
            if not code or code == "0x":
                return "eoa"
            code_lower = code.lower()
            # EIP-1167 minimal proxy: starts with 363d3d373d3d3d363d73 and ends with 5af43d82803e903d91602b57fd5bf3
            if code_lower.startswith("0x363d3d373d3d3d363d73"):
                return "poly_proxy"
            # Gnosis Safe proxies are ~53 bytes and forward to a master copy stored in slot 0.
            return "gnosis_safe" if len(code_lower) > 2 else "unknown"
        except Exception:
            continue
    return "unknown"


def preflight_signature_type(
    host: str,
    chain_id: int,
    funder: str | None,
    signature_type: int,
) -> None:
    if not funder:
        return
    kind = fetch_funder_kind(host, chain_id, funder)
    expected = {"eoa": 0, "poly_proxy": 1, "gnosis_safe": 2}.get(kind)
    label = {0: "EOA", 1: "POLY_PROXY", 2: "POLY_GNOSIS_SAFE", 3: "POLY_1271"}.get(
        signature_type, str(signature_type)
    )
    print(f"[preflight] funder={funder} on-chain kind={kind} configured signature_type={label}")
    if expected is not None and expected != signature_type:
        expected_label = {0: "EOA", 1: "POLY_PROXY", 2: "POLY_GNOSIS_SAFE"}[expected]
        raise RuntimeError(
            f"SIGNATURE_TYPE mismatch: funder {funder} looks like {kind} on-chain, "
            f"but SIGNATURE_TYPE is {label}. Set POLYMARKET_SIGNATURE_TYPE={expected_label}."
        )


def new_client(
    host: str,
    chain_id: int,
    private_key: str,
    signature_type: int,
    funder: str | None,
    creds: ApiCreds | None = None,
) -> ClobClient:
    return ClobClient(
        host=host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=signature_type,
        funder=funder,
        builder_config=maybe_builder_config(),
        use_server_time=True,
    )


def derive_creds(
    host: str,
    initial_chain_id: int,
    private_key: str,
    signature_type: int,
    funder: str | None,
) -> tuple[int, ApiCreds]:
    auth_errors: list[tuple[int, str]] = []
    resolved_creds: ApiCreds | None = None
    resolved_chain_id: int | None = None

    print("Deriving CLOB API credentials from PK...")
    for chain_id in candidate_chain_ids(initial_chain_id):
        bootstrap_client = new_client(
            host=host,
            chain_id=chain_id,
            private_key=private_key,
            signature_type=signature_type,
            funder=funder,
        )
        address = bootstrap_client.get_address()
        print(f"[auth] trying address={address} chain_id={chain_id} use_server_time=true")
        try:
            try:
                resolved_creds = bootstrap_client.create_api_key()
                print("[auth] create_api_key succeeded")
            except Exception as exc_create:
                print(f"[auth] create_api_key failed ({exc_create}); falling back to derive_api_key")
                resolved_creds = bootstrap_client.derive_api_key()
                print("[auth] derive_api_key succeeded (deterministic creds for this EOA)")
            resolved_chain_id = chain_id
            break
        except Exception as exc:
            auth_errors.append((chain_id, str(exc)))

    if resolved_creds is None or resolved_chain_id is None:
        attempts = "; ".join(
            f"chain_id={chain_id}: {message}" for chain_id, message in auth_errors
        )
        raise RuntimeError(
            "could not derive Polymarket API credentials from PK. "
            f"Tried {attempts}. "
            "Most likely causes: "
            "1) wrong chain id for this host, "
            "2) PK is not the actual signing key for this Polymarket account, "
            "3) this is a proxy/safe wallet and you also need the correct signer key plus FUNDER/SIGNATURE_TYPE, "
            "or 4) you should reuse existing CLOB_API_KEY/CLOB_SECRET/CLOB_PASS_PHRASE instead of deriving."
        )

    return resolved_chain_id, resolved_creds


def l2_preflight(client: ClobClient) -> None:
    client.get_api_keys()


def build_client(args: argparse.Namespace) -> ClobClient:
    private_key = require_any_env(
        "PK",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_USER_PRIVATE_KEY",
    )
    signature_type = parse_signature_type(
        env_first("SIGNATURE_TYPE", "POLYMARKET_SIGNATURE_TYPE"),
        default=0,
    )
    funder = env_first(
        "FUNDER",
        "POLYMARKET_FUNDER",
        "POLYMARKET_USER_FUNDER_ADDRESS",
    )
    preflight_signature_type(args.host, args.chain_id, funder, signature_type)
    builder_code = env_builder_code()
    if builder_code:
        verify_builder_code(args.host, builder_code)
    creds = maybe_load_creds_from_env()
    resolved_chain_id = args.chain_id

    if creds is not None:
        client = new_client(
            host=args.host,
            chain_id=resolved_chain_id,
            private_key=private_key,
            signature_type=signature_type,
            funder=funder,
            creds=creds,
        )
        print(
            f"Using CLOB API credentials from environment for address {client.get_address()} "
            "(set FORCE_DERIVE=1 to ignore them)"
        )
        try:
            l2_preflight(client)
            client.get_version()
            return client
        except Exception as exc:
            print(f"[auth] environment L2 credentials failed preflight: {exc}")
            print(
                "[auth] if these are builder-program credentials, they cannot replace user "
                "CLOB API credentials. Falling back to user credential derivation from PK."
            )

    resolved_chain_id, resolved_creds = derive_creds(
        host=args.host,
        initial_chain_id=args.chain_id,
        private_key=private_key,
        signature_type=signature_type,
        funder=funder,
    )

    if resolved_chain_id != args.chain_id:
        print(
            f"[auth] chain_id={args.chain_id} failed, but chain_id={resolved_chain_id} succeeded. "
            "Continuing with the successful chain id."
        )

    client = new_client(
        host=args.host,
        chain_id=resolved_chain_id,
        private_key=private_key,
        signature_type=signature_type,
        funder=funder,
        creds=resolved_creds,
    )
    l2_preflight(client)
    client.get_version()
    return client


def get_book_field(book: dict[str, Any], key: str, default: Any = None) -> Any:
    value = book.get(key, default)
    return default if value in (None, "") else value


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value))
    return str(value)


def compact_json(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def parse_errno(message: str) -> int | None:
    match = re.search(r"\[Errno (\d+)\]", message)
    if not match:
        return None
    return int(match.group(1))


def transport_error_message(payload: dict[str, Any]) -> str:
    sdk_message = payload.get("sdk_http_error")
    if sdk_message:
        return sdk_message
    return payload.get("error", "")


def find_first_value(payload: Any, keys: tuple[str, ...]) -> Any | None:
    if hasattr(payload, "__dict__"):
        payload = vars(payload)

    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return value
        for value in payload.values():
            found = find_first_value(value, keys)
            if found not in (None, "", [], {}):
                return found
        return None

    if isinstance(payload, list):
        for item in payload:
            found = find_first_value(item, keys)
            if found not in (None, "", [], {}):
                return found

    return None


def summarize_balance_allowance(payload: dict[str, Any]) -> str:
    balance = find_first_value(
        payload,
        ("balance", "available", "availableBalance", "available_balance"),
    )
    allowance = find_first_value(
        payload,
        ("allowance", "approved", "availableAllowance", "available_allowance"),
    )
    parts = []
    if balance is not None:
        parts.append(f"balance={balance}")
    if allowance is not None:
        parts.append(f"allowance={allowance}")
    return ", ".join(parts) if parts else "raw only"


def best_price(levels: list[dict[str, Any]], side: str) -> Decimal | None:
    if not levels:
        return None
    prices = [Decimal(str(level["price"])) for level in levels]
    return min(prices) if side == BUY else max(prices)


def choose_passive_price(side: str, tick_size: Decimal) -> Decimal:
    if side == BUY:
        return tick_size
    return Decimal("1") - tick_size


def resolve_order_config(
    client: ClobClient,
    token_id: str,
    side: str,
    user_price: Decimal | None,
    user_size: Decimal | None,
) -> tuple[Decimal, Decimal, PartialCreateOrderOptions, dict[str, Any]]:
    book = client.get_order_book(token_id)
    tick_size = Decimal(str(get_book_field(book, "tick_size")))
    min_order_size = Decimal(str(get_book_field(book, "min_order_size", "5")))
    neg_risk = bool(get_book_field(book, "neg_risk", False))

    price = user_price if user_price is not None else choose_passive_price(side, tick_size)
    size = user_size if user_size is not None else min_order_size

    best_ask = best_price(get_book_field(book, "asks", []), BUY)
    best_bid = best_price(get_book_field(book, "bids", []), SELL)

    if side == BUY and best_ask is not None and price >= best_ask:
        raise RuntimeError(
            f"default/selected BUY price {price} crosses best ask {best_ask}; "
            "pass an explicit lower --price"
        )
    if side == SELL and best_bid is not None and price <= best_bid:
        raise RuntimeError(
            f"default/selected SELL price {price} crosses best bid {best_bid}; "
            "pass an explicit higher --price"
        )

    options = PartialCreateOrderOptions(
        tick_size=decimal_to_str(tick_size),
        neg_risk=neg_risk,
    )
    return price, size, options, book


def get_balance_allowance_preflight(
    client: ClobClient,
    token_id: str,
    side: str,
) -> dict[str, dict[str, Any]]:
    def fetch(asset_type: str, *, token_id_value: str | None = None) -> dict[str, Any]:
        params = BalanceAllowanceParams(
            asset_type=asset_type,
            token_id=token_id_value,
        )
        try:
            refreshed = client.update_balance_allowance(params)
            if refreshed:
                return to_jsonable(refreshed)
        except Exception:
            pass
        return to_jsonable(client.get_balance_allowance(params))

    snapshot = {
        "collateral": fetch(AssetType.COLLATERAL)
    }
    if side == SELL:
        snapshot["conditional"] = fetch(
            AssetType.CONDITIONAL,
            token_id_value=token_id,
        )
    return snapshot


def print_balance_allowance_preflight(snapshot: dict[str, dict[str, Any]]) -> None:
    print("Balance / allowance preflight:")
    for label, payload in snapshot.items():
        print(f"  {label}: {summarize_balance_allowance(payload)}")
        print(f"  {label} raw: {compact_json(payload)}")


def snapshot_open_orders(client: ClobClient, token_id: str) -> dict[str, dict[str, Any]]:
    orders = client.get_open_orders(OpenOrderParams(asset_id=token_id))
    return {order["id"]: order for order in orders}


def build_shared_timestamp_orders(
    client: ClobClient,
    token_id: str,
    price: Decimal,
    size: Decimal,
    side: str,
    options: PartialCreateOrderOptions,
    count: int,
) -> tuple[int, list[Any]]:
    shared_timestamp_ms = time.time_ns() // 1_000_000
    shared_timestamp_ns = shared_timestamp_ms * 1_000_000
    sdk_side = Side.BUY if side == BUY else Side.SELL

    orders: list[Any] = []
    with patch(
        "py_clob_client_v2.order_builder.builder.time.time_ns",
        return_value=shared_timestamp_ns,
    ):
        for _ in range(count):
            order_args_kwargs: dict[str, Any] = {
                "token_id": token_id,
                "price": float(price),
                "side": sdk_side,
                "size": float(size),
            }
            builder_code = env_builder_code()
            if builder_code:
                order_args_kwargs["builder_code"] = builder_code
            order = client.create_order(
                order_args=OrderArgs(**order_args_kwargs),
                options=options,
            )
            orders.append(order)
    return shared_timestamp_ms, orders


def build_exact_duplicate_orders(
    client: ClobClient,
    token_id: str,
    price: Decimal,
    size: Decimal,
    side: str,
    options: PartialCreateOrderOptions,
    count: int,
) -> tuple[int, list[Any]]:
    shared_timestamp_ms, orders = build_shared_timestamp_orders(
        client=client,
        token_id=token_id,
        price=price,
        size=size,
        side=side,
        options=options,
        count=1,
    )
    template_order = orders[0]
    duplicated_orders = [copy.deepcopy(template_order) for _ in range(count)]
    return shared_timestamp_ms, duplicated_orders


def build_orders_for_burst(
    client: ClobClient,
    token_id: str,
    price: Decimal,
    size: Decimal,
    side: str,
    options: PartialCreateOrderOptions,
    count: int,
    burst_mode: str,
) -> tuple[int, list[Any]]:
    if burst_mode == EXACT_DUPLICATE:
        return build_exact_duplicate_orders(
            client=client,
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            options=options,
            count=count,
        )
    if burst_mode == SHARED_TIMESTAMP:
        return build_shared_timestamp_orders(
            client=client,
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            options=options,
            count=count,
        )
    raise ValueError(f"unsupported burst mode: {burst_mode}")


def extract_error_text(response: Any) -> str:
    if isinstance(response, BaseException):
        return str(response)
    if not isinstance(response, dict):
        return ""

    for key in ("errorMsg", "error"):
        value = response.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and value:
            return json.dumps(value, sort_keys=True)
    return ""


def response_kind(response: Any) -> str:
    error_text = extract_error_text(response)
    if "INVALID_ORDER_DUPLICATED" in error_text or "Duplicated" in error_text:
        return "duplicate"
    if "Request exception!" in error_text or "Server disconnected" in error_text:
        return "transport_error"
    if isinstance(response, dict) and response.get("success") is True:
        return "success"
    if error_text:
        return "error"
    return "unknown"


def post_one(
    client: ClobClient,
    order: Any,
    post_only: bool,
    barrier: threading.Barrier,
    index: int,
) -> dict[str, Any]:
    thread_id = threading.get_ident()
    log_mark = SDK_HTTP_ERROR_CAPTURE.mark()
    try:
        barrier.wait()
    except threading.BrokenBarrierError as exc:
        return {
            "index": index,
            "latency_ms": 0.0,
            "timestamp_ms": int(order.timestamp),
            "salt": str(order.salt),
            "kind": "internal_error",
            "error": str(exc),
            "exception_type": type(exc).__name__,
            "success": False,
            "response": {"exception": str(exc)},
        }
    started_ns = time.perf_counter_ns()
    exception_obj: BaseException | None = None
    try:
        response = client.post_order(order, OrderType.GTC, post_only=post_only)
    except Exception as exc:
        exception_obj = exc
        response = exc
    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    sdk_http_error = SDK_HTTP_ERROR_CAPTURE.latest_since(thread_id, log_mark)
    sdk_http_error_message = sdk_http_error["message"] if sdk_http_error else None

    payload: dict[str, Any] = {
        "index": index,
        "latency_ms": round(elapsed_ms, 3),
        "timestamp_ms": int(order.timestamp),
        "salt": str(order.salt),
        "kind": response_kind(response),
        "error": extract_error_text(response),
    }
    if exception_obj is not None:
        payload["exception_type"] = type(exception_obj).__name__
    if sdk_http_error_message:
        payload["sdk_http_error"] = sdk_http_error_message
        errno_value = parse_errno(sdk_http_error_message)
        if errno_value is not None:
            payload["sdk_http_errno"] = errno_value

    if isinstance(response, dict):
        payload["response"] = response
        payload["order_id"] = response.get("orderID")
        payload["status"] = response.get("status")
        payload["success"] = response.get("success")
    else:
        payload["response"] = {"exception": str(response)}
        payload["success"] = False

    return payload


def summarise_requests(requests_payload: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [item["latency_ms"] for item in requests_payload]
    fastest_success = [
        item["latency_ms"]
        for item in requests_payload
        if item.get("success") is True
    ]

    counts: dict[str, int] = {}
    for item in requests_payload:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    summary = {
        "request_counts": counts,
        "latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "max": round(max(latencies), 3),
        },
        "fastest_success_ms": round(min(fastest_success), 3) if fastest_success else None,
    }
    return summary


def summarize_float_samples(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"sample_count": 0, "min": None, "median": None, "max": None}
    rounded = [round(value, 3) for value in values]
    return {
        "sample_count": len(rounded),
        "min": round(min(rounded), 3),
        "median": round(statistics.median(rounded), 3),
        "max": round(max(rounded), 3),
    }


def cleanup_orders(client: ClobClient, order_ids: list[str]) -> dict[str, Any] | None:
    if not order_ids:
        return None
    log_mark = SDK_HTTP_ERROR_CAPTURE.mark()
    try:
        return {
            "success": True,
            "response": client.cancel_orders(order_ids),
        }
    except Exception as exc:
        payload: dict[str, Any] = {
            "success": False,
            "error": extract_error_text(exc),
            "exception_type": type(exc).__name__,
        }
        sdk_http_error = SDK_HTTP_ERROR_CAPTURE.latest_since(threading.get_ident(), log_mark)
        if sdk_http_error is not None:
            payload["sdk_http_error"] = sdk_http_error["message"]
        return payload


def run_burst(
    client: ClobClient,
    token_id: str,
    side: str,
    price: Decimal,
    size: Decimal,
    options: PartialCreateOrderOptions,
    fanout: int,
    burst_mode: str,
    post_only: bool,
    cleanup: bool,
    settle_seconds: float,
) -> dict[str, Any]:
    before = snapshot_open_orders(client, token_id)
    shared_timestamp_ms, orders = build_orders_for_burst(
        client=client,
        token_id=token_id,
        price=price,
        size=size,
        side=side,
        options=options,
        count=fanout,
        burst_mode=burst_mode,
    )
    unique_timestamps = sorted({int(order.timestamp) for order in orders})
    unique_salts = sorted({str(order.salt) for order in orders})

    barrier = threading.Barrier(fanout + 1)
    requests_payload: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=fanout) as executor:
        futures = [
            executor.submit(post_one, client, order, post_only, barrier, index)
            for index, order in enumerate(orders, start=1)
        ]
        barrier.wait()
        for future in as_completed(futures):
            requests_payload.append(future.result())

    requests_payload.sort(key=lambda item: item["index"])
    time.sleep(settle_seconds)

    after = snapshot_open_orders(client, token_id)
    new_order_ids = sorted(set(after) - set(before))
    new_orders = [after[order_id] for order_id in new_order_ids]
    cleanup_result = cleanup_orders(client, new_order_ids) if cleanup else None
    client_success_count = sum(1 for item in requests_payload if item.get("success") is True)
    duplicate_reject_count = sum(1 for item in requests_payload if item["kind"] == "duplicate")
    transport_error_count = sum(
        1 for item in requests_payload if item["kind"] == "transport_error"
    )
    winner_landed = len(new_order_ids) > 0
    landed_without_success_response = winner_landed and client_success_count < len(new_order_ids)

    return {
        "fanout": fanout,
        "burst_mode": burst_mode,
        "shared_timestamp_ms": shared_timestamp_ms,
        "unique_timestamp_count": len(unique_timestamps),
        "unique_salt_count": len(unique_salts),
        "unique_timestamps": unique_timestamps,
        "unique_salts": unique_salts,
        "requests": requests_payload,
        "summary": summarise_requests(requests_payload),
        "client_success_count": client_success_count,
        "duplicate_reject_count": duplicate_reject_count,
        "transport_error_count": transport_error_count,
        "winner_landed": winner_landed,
        "landed_without_success_response": landed_without_success_response,
        "new_open_order_ids": new_order_ids,
        "new_open_order_count": len(new_order_ids),
        "new_open_orders": new_orders,
        "cleanup_result": cleanup_result,
    }


def print_burst_summary(result: dict[str, Any]) -> None:
    summary = result["summary"]
    request_counts = summary["request_counts"]
    latency = summary["latency_ms"]
    print("\n" + "=" * 72)
    print(
        f"fanout={result['fanout']} | burst_mode={result['burst_mode']} "
        f"| shared_timestamp_ms={result['shared_timestamp_ms']} "
        f"| unique_timestamps={result['unique_timestamp_count']} "
        f"| unique_salts={result['unique_salt_count']} "
        f"| new_open_orders={result['new_open_order_count']}"
    )
    print(
        "request outcomes: "
        + ", ".join(f"{kind}={count}" for kind, count in sorted(request_counts.items()))
    )
    print(
        f"client_successes={result['client_success_count']} "
        f"| winner_landed={'yes' if result['winner_landed'] else 'no'} "
        f"| landed_without_success_response={'yes' if result['landed_without_success_response'] else 'no'}"
    )
    print(
        f"latency ms: min={latency['min']:.3f} "
        f"median={latency['median']:.3f} max={latency['max']:.3f}"
    )
    if summary["fastest_success_ms"] is not None:
        print(f"fastest success ms: {summary['fastest_success_ms']:.3f}")
    else:
        print("fastest success ms: none")

    for request_payload in result["requests"]:
        transport_detail = ""
        if request_payload["kind"] == "transport_error":
            detail = request_payload.get("sdk_http_error")
            errno_value = request_payload.get("sdk_http_errno")
            if detail:
                transport_detail = f" transport_detail={detail!r}"
            elif errno_value is not None:
                transport_detail = f" transport_errno={errno_value}"
        print(
            "  "
            f"#{request_payload['index']} latency={request_payload['latency_ms']:.3f}ms "
            f"timestamp={request_payload['timestamp_ms']} "
            f"salt={request_payload['salt']} "
            f"kind={request_payload['kind']} status={request_payload.get('status')!r} "
            f"order_id={request_payload.get('order_id')!r} error={request_payload['error']!r}"
            f"{transport_detail}"
        )

    if result["cleanup_result"] is not None:
        print(f"cleanup result: {json.dumps(result['cleanup_result'], sort_keys=True)}")

    transport_messages = [
        transport_error_message(request_payload)
        for request_payload in result["requests"]
        if request_payload["kind"] == "transport_error"
    ]
    if transport_messages:
        counts: dict[str, int] = {}
        for message in transport_messages:
            counts[message] = counts.get(message, 0) + 1
        print("transport error breakdown:")
        for message, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {count}x {message}")

    all_invalid_sig = result["requests"] and all(
        "invalid signature" in (r.get("error") or "") for r in result["requests"]
    )
    if all_invalid_sig:
        print(
            "\n[hint] every order rejected as 'invalid signature'. Polymarket accepted the L2 "
            "auth headers, so the PK is crypto-valid — but the server has no signer→maker "
            "binding for this EOA. Two common causes:\n"
            "  - Managed-builder user: orders must be signed by the BUILDER PK with "
            "POLYMARKET_BUILDER_CODE set (bytes32, not the builder API-key UUID).\n"
            "  - Self-custodial user: the PK must be the actual signer EOA of POLYMARKET_FUNDER. "
            "Re-export it from Polymarket's UI (Settings → Export Private Key)."
        )


def aggregate_repeat_runs(
    repeat_runs: list[dict[str, Any]],
    counts: list[int],
) -> list[dict[str, Any]]:
    aggregate_rows: list[dict[str, Any]] = []
    for fanout in counts:
        matching_results = []
        for repeat_run in repeat_runs:
            result = next(
                (item for item in repeat_run["results"] if item["fanout"] == fanout),
                None,
            )
            if result is not None:
                matching_results.append((repeat_run, result))

        observed_winner_latencies = [
            result["summary"]["fastest_success_ms"]
            for _, result in matching_results
            if result["summary"]["fastest_success_ms"] is not None
        ]
        improvements_vs_repeat_baseline = []
        comparable_repeat_count = 0
        beat_repeat_baseline_count = 0

        for repeat_run, result in matching_results:
            baseline = repeat_run.get("baseline_fastest_success_ms")
            fastest = result["summary"]["fastest_success_ms"]
            if baseline is None or fastest is None:
                continue
            comparable_repeat_count += 1
            delta = baseline - fastest
            improvements_vs_repeat_baseline.append(delta)
            if delta > 0:
                beat_repeat_baseline_count += 1

        repeat_count = len(matching_results)
        winner_landed_count = sum(1 for _, result in matching_results if result["winner_landed"])
        client_success_repeat_count = sum(
            1 for _, result in matching_results if result["client_success_count"] > 0
        )
        landed_without_success_response_count = sum(
            1
            for _, result in matching_results
            if result["landed_without_success_response"]
        )
        duplicate_reject_total = sum(
            result["duplicate_reject_count"] for _, result in matching_results
        )
        transport_error_total = sum(
            result["transport_error_count"] for _, result in matching_results
        )
        orders_landed_total = sum(
            result["new_open_order_count"] for _, result in matching_results
        )

        aggregate_rows.append(
            {
                "fanout": fanout,
                "repeat_count": repeat_count,
                "winner_landed_count": winner_landed_count,
                "winner_landed_rate": (
                    winner_landed_count / repeat_count if repeat_count else 0.0
                ),
                "client_success_repeat_count": client_success_repeat_count,
                "client_success_rate": (
                    client_success_repeat_count / repeat_count if repeat_count else 0.0
                ),
                "landed_without_success_response_count": landed_without_success_response_count,
                "landed_without_success_response_rate": (
                    landed_without_success_response_count / repeat_count if repeat_count else 0.0
                ),
                "duplicate_reject_total": duplicate_reject_total,
                "transport_error_total": transport_error_total,
                "orders_landed_total": orders_landed_total,
                "observed_winner_latency_ms": summarize_float_samples(
                    observed_winner_latencies
                ),
                "improvement_vs_repeat_baseline_ms": summarize_float_samples(
                    improvements_vs_repeat_baseline
                ),
                "comparable_repeat_count": comparable_repeat_count,
                "beat_repeat_baseline_count": beat_repeat_baseline_count,
                "beat_repeat_baseline_rate": (
                    beat_repeat_baseline_count / comparable_repeat_count
                    if comparable_repeat_count
                    else 0.0
                ),
            }
        )
    return aggregate_rows


def print_repeat_header(repeat_index: int, repeats: int) -> None:
    print("\n" + "#" * 72)
    print(f"repeat {repeat_index}/{repeats}")
    print("#" * 72)


def print_repeat_aggregate_summary(aggregate_rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 72)
    print("aggregate by fanout")
    for row in aggregate_rows:
        observed = row["observed_winner_latency_ms"]
        delta = row["improvement_vs_repeat_baseline_ms"]
        observed_text = (
            f"median={observed['median']:.3f}ms"
            if observed["median"] is not None
            else "median=none"
        )
        delta_text = (
            f"median={delta['median']:.3f}ms"
            if delta["median"] is not None
            else "median=none"
        )
        print(
            f"fanout={row['fanout']} "
            f"| winner_landed={row['winner_landed_count']}/{row['repeat_count']} "
            f"| client_success={row['client_success_repeat_count']}/{row['repeat_count']} "
            f"| landed_wo_success={row['landed_without_success_response_count']}/{row['repeat_count']} "
            f"| beat_fanout1={row['beat_repeat_baseline_count']}/{row['comparable_repeat_count']} "
            f"| observed_winner_latency {observed_text} "
            f"| delta_vs_1 {delta_text}"
        )


def maybe_write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nWrote full JSON results to {path}")


def default_summary_path() -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return DEFAULT_OUTPUT_DIR / timestamp / "summary.json"


def main() -> None:
    args = parse_args()
    counts = parse_counts(args.counts)
    require_positive_int("repeats", args.repeats)
    client = build_client(args)
    json_out = args.json_out or default_summary_path()

    price, size, options, book = resolve_order_config(
        client=client,
        token_id=args.token_id,
        side=args.side,
        user_price=args.price,
        user_size=args.size,
    )
    balance_allowance = get_balance_allowance_preflight(
        client=client,
        token_id=args.token_id,
        side=args.side,
    )

    print(f"Host: {args.host}")
    print(f"Requested chain ID: {args.chain_id}")
    print(f"Resolved chain ID: {client.chain_id}")
    if client.chain_id != args.chain_id:
        print(
            "WARNING: requested chain ID differs from the resolved chain ID. "
            "Orders will be submitted using the resolved chain."
        )
    print(f"Token ID: {args.token_id}")
    print(f"Side: {args.side}")
    print(f"Price: {decimal_to_str(price)}")
    print(f"Size: {decimal_to_str(size)}")
    print(f"Tick size: {book.get('tick_size')}")
    print(f"Neg risk: {book.get('neg_risk')}")
    print(f"Best bid: {best_price(get_book_field(book, 'bids', []), SELL)}")
    print(f"Best ask: {best_price(get_book_field(book, 'asks', []), BUY)}")
    print(f"Counts: {counts}")
    print(f"Repeats: {args.repeats}")
    print(f"Burst mode: {args.burst_mode}")
    print(f"Post only: {args.post_only}")
    print(f"Cleanup: {args.cleanup}")
    print_balance_allowance_preflight(balance_allowance)

    repeat_runs = []
    for repeat_index in range(1, args.repeats + 1):
        print_repeat_header(repeat_index, args.repeats)
        results = []
        for fanout in counts:
            result = run_burst(
                client=client,
                token_id=args.token_id,
                side=args.side,
                price=price,
                size=size,
                options=options,
                fanout=fanout,
                burst_mode=args.burst_mode,
                post_only=args.post_only,
                cleanup=args.cleanup,
                settle_seconds=args.settle_seconds,
            )
            results.append(result)
            print_burst_summary(result)

        baseline_fastest = next(
            (
                result["summary"]["fastest_success_ms"]
                for result in results
                if result["fanout"] == 1 and result["summary"]["fastest_success_ms"] is not None
            ),
            None,
        )

        if baseline_fastest is not None:
            print("\n" + "=" * 72)
            print(
                f"repeat {repeat_index}/{args.repeats} baseline fastest success "
                f"(fanout=1): {baseline_fastest:.3f}ms"
            )
            for result in results:
                fastest = result["summary"]["fastest_success_ms"]
                if fastest is None:
                    print(f"fanout={result['fanout']} | no successful order placement")
                    continue
                delta = baseline_fastest - fastest
                print(
                    f"fanout={result['fanout']} | fastest_success={fastest:.3f}ms "
                    f"| improvement_vs_1={delta:.3f}ms"
                )

        repeat_runs.append(
            {
                "repeat_index": repeat_index,
                "baseline_fastest_success_ms": baseline_fastest,
                "results": results,
            }
        )

    aggregate_by_fanout = aggregate_repeat_runs(repeat_runs, counts)
    if args.repeats > 1:
        print_repeat_aggregate_summary(aggregate_by_fanout)

    payload = {
        "host": args.host,
        "chain_id": args.chain_id,
        "token_id": args.token_id,
        "side": args.side,
        "price": decimal_to_str(price),
        "size": decimal_to_str(size),
        "counts": counts,
        "repeats": args.repeats,
        "burst_mode": args.burst_mode,
        "post_only": args.post_only,
        "cleanup": args.cleanup,
        "resolved_chain_id": client.chain_id,
        "balance_allowance_preflight": balance_allowance,
        "results": repeat_runs[0]["results"] if repeat_runs else [],
        "repeat_runs": repeat_runs,
        "aggregate_by_fanout": aggregate_by_fanout,
    }
    maybe_write_json(json_out, payload)


if __name__ == "__main__":
    main()
