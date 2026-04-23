#!/usr/bin/env python3
"""Sanity probe: compare /time endpoint to local clock, then measure
per-event-type freshness on the CLOB WS for one or more assets.

Usage:
  probe_time.py --asset <asset_id> [--duration 300]
  probe_time.py --series 10684    [--duration 300]   # resolves current market
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.request
from collections import defaultdict

import websockets

CLOB_TIME_URL = "https://clob.polymarket.com/time"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_EVENT_TYPES = {"book", "price_change", "last_trade_price"}
HTTP_HEADERS = {
    "user-agent": "polymarket-latency-probe/1.0",
    "accept": "application/json",
}


def probe_time(samples: int = 5) -> None:
    print(f"\n== /time endpoint vs local clock ({samples} samples) ==")
    req = urllib.request.Request(CLOB_TIME_URL, headers=HTTP_HEADERS)
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


def resolve_series_asset(series_id: str) -> tuple[str, str]:
    url = f"{GAMMA_MARKETS_URL}?closed=false&limit=1&series_id={series_id}"
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    markets = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    if not markets:
        raise RuntimeError(f"no active market for series {series_id}")
    market = markets[0]
    token_ids = market.get("clobTokenIds")
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
    if not token_ids:
        raise RuntimeError(f"no clobTokenIds on market {market.get('slug')}")
    return market.get("slug") or "", str(token_ids[0])


async def probe_ws(asset_id: str, duration: float) -> None:
    print(f"\n== WS freshness, {duration:.0f}s on asset {asset_id[:12]}... ==")
    freshness_ms: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    bucket_p95: list[tuple[int, dict[str, float]]] = []
    bucket: dict[str, list[float]] = defaultdict(list)
    start = time.time()
    bucket_start = start
    bucket_seconds = 30.0
    deadline = start + duration

    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        await ws.send(json.dumps({"type": "market", "assets_ids": [asset_id]}))
        last_msg_at = start
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            now = time.time()
            received_ms = now * 1000
            gap = now - last_msg_at
            last_msg_at = now
            if gap >= 3.0:
                print(f"  [+{now - start:5.1f}s] GAP: {gap:.2f}s since last message")
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
                fresh = received_ms - ts_ms
                freshness_ms[et].append(fresh)
                bucket[et].append(fresh)
            if now - bucket_start >= bucket_seconds:
                snapshot = {}
                for et, vs in bucket.items():
                    if vs:
                        svs = sorted(vs)
                        snapshot[et] = svs[int(len(svs) * 0.95)]
                bucket_p95.append((int(now - start), snapshot))
                bucket.clear()
                bucket_start = now

    print(f"\n  overall ({int(time.time() - start)}s):")
    print(f"  {'event_type':20s}  {'n':>7s}  {'p50_ms':>9s}  {'p95_ms':>9s}  {'p99_ms':>9s}  {'max_ms':>10s}")
    for et in sorted(freshness_ms):
        vs = sorted(freshness_ms[et])
        if not vs:
            continue
        p50 = vs[len(vs) // 2]
        p95 = vs[int(len(vs) * 0.95)]
        p99 = vs[int(len(vs) * 0.99)]
        print(f"  {et:20s}  {counts[et]:>7d}  {p50:>9.0f}  {p95:>9.0f}  {p99:>9.0f}  {max(vs):>10.0f}")

    if bucket_p95:
        print(f"\n  per-{bucket_seconds:.0f}s p95 (to see if freshness drifts over time):")
        for ts_s, snap in bucket_p95:
            parts = "  ".join(f"{et}={v:.0f}ms" for et, v in sorted(snap.items()))
            print(f"    [+{ts_s:4d}s] {parts}")


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--asset", help="CLOB asset/token id (long decimal)")
    g.add_argument("--series", help="Gamma series_id (e.g. 10684); resolves current active market")
    ap.add_argument("--duration", type=float, default=300.0, help="WS probe seconds (default 300)")
    args = ap.parse_args()

    probe_time()
    if args.series:
        slug, asset_id = resolve_series_asset(args.series)
        print(f"\nresolved series {args.series} -> {slug} (token {asset_id[:16]}...)")
    else:
        asset_id = args.asset
    asyncio.run(probe_ws(asset_id, args.duration))


if __name__ == "__main__":
    main()
