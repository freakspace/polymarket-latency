#!/usr/bin/env python3
"""Probe Polymarket WS per-IP concurrent-connection limit.

Opens N concurrent WS connections to the same market token, holds them for
--duration, and reports disconnect activity. No aggregator, no recording,
no rotation — isolates venue-side kick behavior from local code paths.

Each connection auto-reconnects after a kick (with a short backoff) so the
test maintains pressure at N for the full duration.

Usage:
  connection_limit_probe.py --connections 25 --duration 300 --series 10684
  connection_limit_probe.py --connections 25 --duration 300 --asset <token_id>
  connection_limit_probe.py --sweep 5,10,15,20,25,30 --duration 180 --series 10684
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
HTTP_HEADERS = {
    "user-agent": "polymarket-latency-probe/1.0",
    "accept": "application/json",
}


@dataclass
class ConnectionStats:
    conn_id: int
    connects: int = 0
    disconnects: int = 0
    messages: int = 0
    first_connect_at: Optional[float] = None
    disconnect_times_relative: list[float] = field(default_factory=list)
    last_disconnect_reason: Optional[str] = None
    currently_connected: bool = False


def resolve_series_asset(series_id: str) -> tuple[str, str]:
    url = f"{GAMMA_MARKETS_URL}?closed=false&limit=50&series_id={series_id}"
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    markets = json.loads(urllib.request.urlopen(req, timeout=10).read())
    for market in markets:
        slug = market.get("slug") or ""
        tids = market.get("clobTokenIds")
        if isinstance(tids, str):
            tids = json.loads(tids)
        if tids:
            return slug, str(tids[0])
    raise RuntimeError(f"no usable market for series {series_id}")


async def hold_connection(
    conn_id: int,
    asset_id: str,
    deadline: float,
    stats: ConnectionStats,
    test_started: float,
) -> None:
    while time.time() < deadline:
        connected = False
        last_exc_type: Optional[str] = None
        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                open_timeout=10.0,
            ) as ws:
                connected = True
                stats.connects += 1
                stats.currently_connected = True
                if stats.first_connect_at is None:
                    stats.first_connect_at = time.time()
                await ws.send(
                    json.dumps({"type": "market", "assets_ids": [asset_id]})
                )
                while time.time() < deadline:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=5.0)
                        stats.messages += 1
                    except asyncio.TimeoutError:
                        continue
        except Exception as exc:
            last_exc_type = type(exc).__name__
        finally:
            stats.currently_connected = False
            if connected and last_exc_type is not None:
                stats.disconnects += 1
                stats.disconnect_times_relative.append(time.time() - test_started)
                stats.last_disconnect_reason = last_exc_type
        if time.time() >= deadline:
            break
        await asyncio.sleep(0.5)


async def run_probe(
    connections: int,
    duration: float,
    asset_id: str,
) -> dict:
    test_started = time.time()
    deadline = test_started + duration
    all_stats = [ConnectionStats(conn_id=i) for i in range(connections)]

    print(
        f"\n== probe N={connections} for {duration:.0f}s on asset {asset_id[:16]}... =="
    )
    tasks = [
        asyncio.create_task(
            hold_connection(i, asset_id, deadline, all_stats[i], test_started)
        )
        for i in range(connections)
    ]

    next_report = test_started + 30.0
    while time.time() < deadline:
        sleep_for = min(next_report - time.time(), deadline - time.time())
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        if time.time() >= next_report:
            elapsed = time.time() - test_started
            connected = sum(1 for s in all_stats if s.currently_connected)
            total_disc = sum(s.disconnects for s in all_stats)
            total_msgs = sum(s.messages for s in all_stats)
            print(
                f"  [+{elapsed:5.0f}s] connected={connected}/{connections} "
                f"disconnects={total_disc} msgs={total_msgs}"
            )
            next_report += 30.0

    await asyncio.gather(*tasks, return_exceptions=True)

    total_disc = sum(s.disconnects for s in all_stats)
    affected = sum(1 for s in all_stats if s.disconnects > 0)
    survived = sum(1 for s in all_stats if s.disconnects == 0)
    first_disc_times = [
        s.disconnect_times_relative[0]
        for s in all_stats
        if s.disconnect_times_relative
    ]
    failed_to_connect = [s for s in all_stats if s.connects == 0]

    print(
        f"\n  result: {affected}/{connections} conns kicked at least once "
        f"({survived} survived clean), {total_disc} disconnects total, "
        f"{total_disc / (duration / 60):.2f}/min pool-wide"
    )
    if failed_to_connect:
        print(f"  warn: {len(failed_to_connect)}/{connections} never connected")
    if first_disc_times:
        print(
            f"  time-to-first-kick: min={min(first_disc_times):.1f}s "
            f"median={statistics.median(first_disc_times):.1f}s "
            f"max={max(first_disc_times):.1f}s"
        )

    return {
        "connections": connections,
        "duration_seconds": duration,
        "affected_count": affected,
        "survived_count": survived,
        "failed_to_connect_count": len(failed_to_connect),
        "total_disconnects": total_disc,
        "disconnect_rate_per_min": total_disc / (duration / 60.0),
        "ttf_kick_min": min(first_disc_times) if first_disc_times else None,
        "ttf_kick_median": (
            statistics.median(first_disc_times) if first_disc_times else None
        ),
        "ttf_kick_max": max(first_disc_times) if first_disc_times else None,
        "per_conn": [
            {
                "conn": s.conn_id,
                "connects": s.connects,
                "disconnects": s.disconnects,
                "messages": s.messages,
                "first_disc_at": (
                    s.disconnect_times_relative[0]
                    if s.disconnect_times_relative
                    else None
                ),
                "last_reason": s.last_disconnect_reason,
            }
            for s in all_stats
        ],
    }


def print_sweep_summary(results: list[dict]) -> None:
    print("\n== sweep summary ==")
    print(
        f"{'N':>4s}  {'kicked':>10s}  {'survived':>9s}  {'total_disc':>10s}  "
        f"{'disc/min':>9s}  {'first_kick':>11s}"
    )
    for r in results:
        ttf = r["ttf_kick_min"]
        ttf_label = f"{ttf:.1f}s" if ttf is not None else "—"
        print(
            f"{r['connections']:>4d}  "
            f"{r['affected_count']:>4d}/{r['connections']:<5d}  "
            f"{r['survived_count']:>9d}  "
            f"{r['total_disconnects']:>10d}  "
            f"{r['disconnect_rate_per_min']:>9.2f}  "
            f"{ttf_label:>11s}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--asset", help="CLOB asset/token id")
    target.add_argument("--series", help="Gamma series id; resolves first active market")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--connections", type=int, help="Number of concurrent connections")
    mode.add_argument(
        "--sweep",
        help="Comma-separated N values to test sequentially (e.g. 5,10,15,20,25,30)",
    )
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument(
        "--inter-sweep-delay",
        type=float,
        default=30.0,
        help="Seconds to wait between sweep stages (lets venue state settle).",
    )
    args = ap.parse_args()

    if args.series:
        slug, asset_id = resolve_series_asset(args.series)
        print(f"resolved series {args.series} -> {slug}")
    else:
        asset_id = args.asset

    if args.connections is not None:
        ns = [args.connections]
    else:
        ns = [int(part.strip()) for part in args.sweep.split(",") if part.strip()]

    results: list[dict] = []
    for i, n in enumerate(ns):
        if i > 0 and args.inter_sweep_delay > 0:
            print(f"\n  ... sleeping {args.inter_sweep_delay:.0f}s before next stage ...")
            time.sleep(args.inter_sweep_delay)
        result = asyncio.run(run_probe(n, args.duration, asset_id))
        results.append(result)

    if len(results) > 1:
        print_sweep_summary(results)


if __name__ == "__main__":
    main()
