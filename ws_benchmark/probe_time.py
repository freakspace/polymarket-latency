#!/usr/bin/env python3
"""Sanity probe: compare /time endpoint to local clock, then measure
per-event-type freshness on the CLOB WS for a single asset."""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import urllib.request
from collections import defaultdict

import websockets

CLOB_TIME_URL = "https://clob.polymarket.com/time"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DURATION_SECONDS = 30.0
DEFAULT_EVENT_TYPES = {"book", "price_change", "last_trade_price"}


def probe_time(samples: int = 5) -> None:
    print(f"\n== /time endpoint vs local clock ({samples} samples) ==")
    req = urllib.request.Request(
        CLOB_TIME_URL,
        headers={"user-agent": "polymarket-latency-probe/1.0", "accept": "text/plain"},
    )
    deltas = []
    for _ in range(samples):
        lo = time.time()
        body = urllib.request.urlopen(req, timeout=5).read().decode().strip()
        hi = time.time()
        remote = float(body)
        mid = (lo + hi) / 2
        deltas.append(remote - mid)
        print(
            f"  local_mid={mid:.3f}  remote={remote:.0f}  rtt={1000 * (hi - lo):.1f}ms  "
            f"delta={1000 * (remote - mid):+.0f}ms"
        )
    print(
        f"  summary: median delta = {1000 * statistics.median(deltas):+.0f}ms  "
        f"(remote is integer-seconds so true delta is bounded by RTT/2)"
    )


async def probe_ws(asset_id: str) -> None:
    print(f"\n== WS freshness per event type, {DURATION_SECONDS:.0f}s on asset {asset_id[:12]}... ==")
    freshness_ms: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    deadline = time.time() + DURATION_SECONDS

    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        await ws.send(json.dumps({"type": "market", "assets_ids": [asset_id]}))
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            received_ms = time.time() * 1000
            try:
                events = json.loads(msg)
            except ValueError:
                continue
            if isinstance(events, dict):
                events = [events]
            for ev in events:
                et = ev.get("event_type") or ev.get("type") or ""
                if et not in DEFAULT_EVENT_TYPES:
                    continue
                counts[et] += 1
                ts = ev.get("timestamp")
                try:
                    ts_ms = float(ts)
                except (TypeError, ValueError):
                    continue
                freshness_ms[et].append(received_ms - ts_ms)

    print(f"  {'event_type':20s}  {'n':>6s}  {'p50_ms':>9s}  {'p95_ms':>9s}  {'max_ms':>9s}")
    for et in sorted(freshness_ms):
        vs = sorted(freshness_ms[et])
        if not vs:
            continue
        p50 = vs[len(vs) // 2]
        p95 = vs[int(len(vs) * 0.95)]
        print(f"  {et:20s}  {counts[et]:>6d}  {p50:>9.0f}  {p95:>9.0f}  {max(vs):>9.0f}")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: probe_time.py <asset_id>", file=sys.stderr)
        sys.exit(2)
    probe_time()
    asyncio.run(probe_ws(sys.argv[1]))


if __name__ == "__main__":
    main()
