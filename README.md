# Polymarket Latency Toolkit

A set of tools for measuring, benchmarking, and reasoning about Polymarket CLOB latency end-to-end: WebSocket market data, CLOB V2 order submission, and a topology-scaling benchmark with an HTML/Next.js dashboard.

## What's in here

| Tool | What it answers |
|---|---|
| `ws_benchmark/benchmark.py` | **How fast is your server receiving market data?** Compares 1/2/5/10-socket pools across the same wall-clock window and reports coverage, freshness, arrival delta, gap runs, and per-socket stall evidence. |
| `ws_benchmark/probe_time.py` | **Is my clock aligned with Polymarket's, and is the feed healthy right now?** Compares `clob.polymarket.com/time` to local, then subscribes to one asset and prints per-30s freshness by event type. |
| `polymarket_latency.py` | **Quick one-shot latency probe.** Connects to the market WS, collects N events, prints median/mean/p95 with optional clock-offset calibration for unsynced dev machines. |
| `polymarket_order_burst.py` | **Do duplicate CLOB V2 orders race each other usefully?** Signs N orders with the same V2 timestamp and fires them concurrently at `/order`, reports per-request latency, which landed, and a best-of-N vs single-submit comparison. |
| `wrap_usdce_to_pusd.py` | Helper: wrap USDC.e to pUSD on-chain for CLOB V2 balance prep. |
| `reports/` | Next.js dashboard that renders any `summary.json` produced by `ws_benchmark` as a browseable report. |
| `sync-clock.sh` | One-shot Ubuntu/chrony clock sync (legacy — AWS Time Sync on EC2 is usually already accurate to microseconds). |

## Layout

```
polymarket-latency/
├── Makefile                 # primary entry points (benchmark, report, server, web, order-burst)
├── polymarket_latency.py    # single-run WS latency probe
├── polymarket_order_burst.py# CLOB V2 duplicate-order burst probe
├── wrap_usdce_to_pusd.py
├── sync-clock.sh
├── ws_benchmark/
│   ├── benchmark.py         # topology-scaling WS benchmark
│   ├── probe_time.py        # /time endpoint + per-event-type freshness probe
│   ├── html_generator.py    # renders summary.json -> report.html + SVG charts
│   ├── benchmark_config.toml
│   └── README.md
├── reports/                 # Next.js report dashboard (npm)
├── recordings/              # default output root for all run artifacts
│   ├── ws-bench/<timestamp>/{summary.json, report.html, charts/*.svg}
│   └── order-burst/<timestamp>/summary.json
└── requirements.txt
```

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` includes two optional performance packages used automatically by `ws_benchmark/benchmark.py` when present:

- **`uvloop`** — drop-in replacement for the asyncio event loop, 2–4× throughput on socket-heavy workloads.
- **`orjson`** — 5–10× faster JSON parse/serialize, used on the hot recv path and inside `build_event_key`.

Without them the benchmark falls back to stdlib — it still runs, just slower. The first line of benchmark output reports which are active:

```
[perf] loop=uvloop orjson=yes uvloop=yes
```

## The three latency metrics, and which to trust

The benchmark (and the probe) report three related but distinct numbers. Mixing them up is the easiest way to misread a result.

| Metric | Formula | What it means |
|---|---|---|
| **Freshness** | `received_at_ns − venue_timestamp_ns` | End-to-end latency from Polymarket's engine to your process. **This is "how stale is the data I'm about to act on".** Covers internal fanout + Cloudflare + network + kernel + Python parse. |
| **Arrival delta** | `this_topology_received_at − first_topology_received_at` (same event) | Purely relative: how much later this pool saw an event vs whichever pool saw it first. Measures whether redundancy helps, not absolute latency. |
| **Book age** | same as freshness, but for `book` events only | Polymarket's `book.timestamp` is the *last-book-change* time, not an emit time. Tracked separately as `book_age_ms` so it doesn't poison the freshness metric. |

`Freshness` is computed over `price_change` and `last_trade_price` only. `book` events are excluded because mixing them silently turns the freshness tail into "how long since the last book change" rather than "how late was this message".

## Running the WS benchmark

```bash
make benchmark
# or, with overrides:
venv/bin/python ws_benchmark/benchmark.py --duration 300
```

Configuration lives in `ws_benchmark/benchmark_config.toml`. Keys worth knowing:

| Key | Meaning |
|---|---|
| `series_id` / `market_slug` / `token_ids` | What to subscribe to. `series_id` with automatic rebinding is recommended for rolling-window markets like `btc-updown-5m`. |
| `duration` | Total benchmark wall-clock seconds. Scored metrics exclude warmup. |
| `topologies` | List of pool sizes to compare, e.g. `[1, 2, 5, 10]`. The benchmark opens *all* pools concurrently on the same window. |
| `warmup_seconds` | Per-connection warmup window; events in this window are recorded for the warmup-vs-stable comparison panel but excluded from scored metrics. |
| `event_retention_seconds` | How long the aggregator keeps an event's aggregate around waiting for stragglers before finalizing. If your market has ≥N-second single-socket stalls, raise this to avoid backfill events being re-counted. |
| `event_types` | Defaults to `["book", "price_change", "last_trade_price"]`. |

Environment flags for diagnostics:

```bash
BENCHMARK_ASYNCIO_DEBUG=1 BENCHMARK_SLOW_CALLBACK_MS=200 make benchmark
# Warns on stderr whenever the event loop blocks for more than 200ms.
```

## Rendering reports

The run writes `recordings/ws-bench/<timestamp>/summary.json`. Render it two ways:

**HTML + SVG (static, works offline)**:

```bash
make report                    # interactive picker
make report SUMMARY=recordings/ws-bench/20260423_063906/summary.json
make server                    # pick a run, serve report.html on :8000
```

**Next.js dashboard (interactive)**:

```bash
make web           # build + start on http://127.0.0.1:4242
make web-dev       # HMR dev mode
```

## Running the time/freshness probe

Useful as a sanity check against the benchmark, or to diagnose feed weirdness without the full benchmark load:

```bash
# Probe a specific asset (token) id:
python3 ws_benchmark/probe_time.py --asset <token_id> --duration 300

# Or resolve the current active token for a Gamma series:
python3 ws_benchmark/probe_time.py --series 10684 --duration 300
```

Output includes:
- `/time` delta between Polymarket's server clock and your local clock (sub-second when clocks are NTP-synced on both ends).
- Per-30s p95 buckets of freshness by event type, to see if the feed drifts over time or stalls.
- `GAP: Ns since last message` markers when silence exceeds 3 seconds — helps distinguish "quiet market" (gap followed by *fresh* event) from "stall then flush" (gap followed by *stale* backlog).

## CLOB V2 order burst test

A separate experiment in `polymarket_order_burst.py`. It signs N orders and fires them concurrently at `POST /order` on `clob-v2.polymarket.com` to measure:

- Per-request latency and returned status/error
- How many new open orders actually appeared after the burst
- Best-of-N winner latency vs the `fanout=1` baseline — i.e. does adding duplicates actually reduce observed time-to-book?

### Secrets setup

Auto-loads a local `.env` if present. At minimum you need a funded EOA private key on Polygon Amoy (test) or mainnet:

```bash
export PK=0xyour_private_key
# optional if reusing an existing API creds tuple:
export CLOB_API_KEY=...
export CLOB_SECRET=...
export CLOB_PASS_PHRASE=...
export CHAIN_ID=80002
export CLOB_API_URL=https://clob-v2.polymarket.com
```

### Run

```bash
make order-burst \
  TOKEN_ID=102936224134271070189104847090829839924697394514566827387181305960175107677216 \
  COUNTS=1,2,5,10 \
  REPEATS=10 \
  BURST_MODE=exact-duplicate \
  PRICE=0.01 \
  SIZE=5 \
  CLEANUP=1
```

`BURST_MODE=exact-duplicate` (default) resends the same signed payload N times. `BURST_MODE=shared-timestamp` signs N distinct orders sharing only the V2 timestamp. Writes `recordings/order-burst/<timestamp>/summary.json`.

## Clock sync — what you actually need

The legacy `sync-clock.sh` was written for VPS hosts that had drifted clocks. On modern EC2 instances, AWS's local PTP/chrony feed (`169.254.169.123`) keeps you within microseconds out of the box — `chronyc tracking` on a fresh Ubuntu 22.04 AMI typically shows `System time : 0.000001s slow of NTP time`. You don't need to run anything.

Verify:

```bash
timedatectl status                    # look for "System clock synchronized: yes"
chronyc tracking 2>/dev/null | head   # microsecond offsets expected
date -u; curl -sI https://ws-subscriptions-clob.polymarket.com/ | grep -i '^date'
# The two Date values should be within 1s of each other.
```

`polymarket_latency.py` still supports a calibration mode (`calibration_events > 0`) for WSL/unsynced dev laptops, but for VPS measurements leave it at 0.

## Observed numbers (for reference, not promises)

Two reference points from testing against `btc-updown-5m` (series `10684`), 60-second runs with full 1/2/5/10 topology sweep:

**EC2 `c7a.large` in `eu-west-1` (Dublin), 0.88ms ping to Cloudflare DUB**

| Topology | Coverage | Freshness P50 | Freshness P95 | Arrival P95 |
|----------|---------:|--------------:|--------------:|------------:|
| 1 ws     | 100%     | 10.2 ms       | 129.7 ms      | 26.8 ms     |
| 2 ws     | 100%     |  9.6 ms       | 124.8 ms      | 26.2 ms     |
| 5 ws     | 100%     |  9.0 ms       |  99.0 ms      |  4.9 ms     |
| 10 ws    | 100%     |  **8.4 ms**   |  **95.9 ms**  | **0.5 ms**  |

**Local macOS (residential wifi, thousands of km away)**

| Topology | Coverage | Freshness P50 | Freshness P95 | Arrival P95 |
|----------|---------:|--------------:|--------------:|------------:|
| 1 ws     | 100%     | 54.7 ms       | 161.4 ms      | 37.4 ms     |
| 10 ws    | 99.9%    | 51.0 ms       | 123.7 ms      |  1.5 ms     |

The server's P50 (8–10 ms) is genuinely "as fast as physics allows" from that location: 0.88 ms × 2 of network + Polymarket/Cloudflare internal fanout. More sockets buy a tighter P95 tail (fewer single-socket stalls), not a lower median.

## Troubleshooting — if your benchmark looks broken

A short decision tree that maps to what we've seen:

1. **Freshness P95 in the *seconds*** → almost never network. Check, in order:
   - `ws_benchmark/probe_time.py --series <id> --duration 300` should show `price_change` p95 in the tens of ms. If probe is clean but benchmark is not, it's **CPU saturation** — the Python event loop is falling behind the event rate and `recv()` is returning frames the kernel got seconds ago.
   - `top` the benchmark process. If Python is pegged at ~100% of one vCPU for minutes, the single-core is your ceiling. Install `orjson` + `uvloop` (already in `requirements.txt`) and watch the first-line `[perf]` confirmation. On a burstable `t3.micro` this is typical once CPU credits deplete.
2. **Freshness grows with `--duration`** → backlog accumulating, same root cause as above.
3. **P50 is clean but P95 is multi-second** → brief bursts still overwhelm one vCPU. Upgrade to a non-burstable instance (`c7a.large` is $0.11/hr on-demand in eu-west-1 and sufficient).
4. **Freshness is ~20s even with `book` excluded** → you have socket stalls longer than `event_retention_seconds` (default 15 s). Backfill events land in new aggregates with old timestamps. Raise retention above your observed max single-socket stall, or fix the upstream stall.
5. **Uniform ~20s freshness across all topologies** → previously this was caused by `book` events contaminating the distribution. Confirm the run used the current code (caveats in `summary.json` should mention "book events carry a last-changed timestamp ... reported separately as book_age_ms").

## References

- [Polymarket WebSocket docs](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [Market channel docs](https://docs.polymarket.com/developers/CLOB/websocket/market-channel)
- [`GET /time` endpoint](https://docs.polymarket.com/api-reference/data/get-server-time.md)
- [Get market by slug](https://docs.polymarket.com/api-reference/markets/get-market-by-slug)
- [CLOB V2 migration notes](https://docs.polymarket.com/) — see the V2 API section for the `/order` and `/orders` endpoint semantics used by `polymarket_order_burst.py`.
