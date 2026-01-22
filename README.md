# Polymarket WebSocket Latency Tracker

A Python tool to measure latency between Polymarket websocket events and local receipt time.

**Key Findings:** Testing from an NTP-synced VPS in Amsterdam shows median latency of **~40-42ms** to Polymarket's servers with network variance (std dev) of **~28-48ms**.

## Quick Start (TL;DR)

**For accurate production measurements:**
```bash
# On your VPS
git clone <repo>
cd poly-latency
pip install -r requirements.txt
sudo ./sync-clock.sh  # Answer 'y' for continuous sync

# Run measurement
python polymarket_latency.py btc-updown-15m-1769050800 500 0

# Expected: Median ~40-60ms (Europe), ~10-30ms (US East)
```

**For local/WSL2 testing:**
```bash
# Calibration handles clock offset automatically
python polymarket_latency.py btc-updown-15m-1769050800 100 10
```

## Project Files

```
poly-latency/
├── polymarket_latency.py   # Main latency measurement script
├── sync-clock.sh           # Clock synchronization script (VPS/Ubuntu)
├── requirements.txt        # Python dependencies
├── README.md              # Documentation
└── .gitignore            # Git ignore rules
```

## Overview

This project connects to Polymarket's market websocket, subscribes to a specific market by slug, collects a specified number of events (default: 100), and calculates latency statistics by comparing event timestamps with local receipt times.

## Two Modes of Operation

### Mode 1: NTP-Synced (Recommended for VPS)
- **Use when:** Running on a VPS with NTP time synchronization
- **Calibration:** DISABLED (`calibration_events=0`)
- **What it measures:** True network latency from Polymarket servers to your location
- **Typical results:** 30-100ms median latency depending on geographic location

### Mode 2: Calibrated (For Local/WSL2)
- **Use when:** Running on WSL2 or local machines without NTP sync
- **Calibration:** ENABLED (default: 10 events)
- **What it measures:** Network latency after removing clock offset between systems
- **Typical results:** Small positive/negative values after offset correction

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

## Clock Synchronization (VPS/Ubuntu)

For accurate latency measurements on a VPS, synchronize your system clock with NTP servers:

### One-Time Setup

```bash
# Make the sync script executable
chmod +x sync-clock.sh

# Run the sync script (requires sudo)
sudo ./sync-clock.sh
```

The script will:
- Install `ntpdate` if needed
- Sync your clock with NTP servers
- Optionally enable continuous time synchronization
- Show before/after time status

### Before Each Measurement Session

If you didn't enable continuous sync:
```bash
sudo ./sync-clock.sh
python polymarket_latency.py <market-slug> 100
```

### Verify Clock Sync Status

```bash
timedatectl status
```

Look for `System clock synchronized: yes`

## Usage

```bash
python polymarket_latency.py <market-slug> [num_events] [calibration_events]
```

### Arguments

- `market-slug` (required): The Polymarket market slug (e.g., "btc-updown-15m-1769050800")
- `num_events` (optional): Number of events to collect before closing (default: 100)
- `calibration_events` (optional): Number of initial events to use for clock offset calibration (default: 10)
- `--verbose, -v` (optional): Show detailed output for each event including timestamp gaps

### Examples

**VPS with NTP sync (calibration disabled):**
```bash
python polymarket_latency.py btc-updown-15m-1769050800 500 0
```

**Local machine / WSL2 (calibration enabled):**
```bash
python polymarket_latency.py btc-updown-15m-1769050800 100 10
```

**Quick test with defaults (100 events, 10 calibration events):**
```bash
python polymarket_latency.py will-bitcoin-hit-100k-in-2024
```

**Verbose mode to diagnose batching/queueing:**
```bash
python polymarket_latency.py btc-updown-15m-1769050800 100 0 --verbose
# Shows each event with timestamp gaps to detect batching
```

## How It Works

1. **Fetch Market Info**: Queries Polymarket's REST API to get token IDs for the specified market slug
2. **WebSocket Connection**: Connects to `wss://ws-subscriptions-clob.polymarket.com/ws/market`
3. **Subscribe**: Sends subscription message with the market's token IDs
4. **Clock Offset Calibration**: Uses the first N events (default: 10) to estimate the clock offset between your system and Polymarket's servers
5. **Collect Events**: For each event received:
   - Records the event's timestamp (from Polymarket)
   - Records local receipt time
   - Calculates raw latency = local_time - event_timestamp
   - After calibration: applies offset correction for adjusted latency
6. **Calculate Statistics**: After collecting the specified number of events, displays:
   - Raw measurements (with clock offset)
   - Adjusted measurements (clock offset removed)
   - Median, mean, min, max latency
   - Standard deviation
   - Percentiles (25th, 75th, 95th, 99th)

## Output Examples

### Mode 1: NTP-Synced VPS (No Calibration)

```bash
$ python polymarket_latency.py btc-updown-15m-1769050800 500 0
```

```
Fetching market info for slug: btc-updown-15m-1769050800
Market: Bitcoin Up or Down - January 21, 10:00PM-10:15PM ET
Token IDs: ['54680...', '62534...']

Connecting to WebSocket...
Collecting 500 events...

First event received! Type: price_change
  Raw latency: 42.93ms
  Clock calibration DISABLED - using raw measurements only

Received 10/500 events | Type: price_change | Raw latency: 38.45ms
Received 20/500 events | Type: price_change | Raw latency: 45.32ms
...

============================================================
LATENCY STATISTICS
============================================================

LATENCY MEASUREMENTS (NTP-synced, no calibration):
  Total events: 500
  Median latency: 42.32ms
  Mean latency: 46.01ms
  Min latency: 9.69ms
  Max latency: 132.23ms
  Std deviation: 28.13ms

  Percentiles:
    25th: 27.15ms
    75th: 58.92ms
    95th: 95.44ms
    99th: 118.32ms

  Interpretation:
    Median latency of 42.32ms represents the typical time
    from when Polymarket creates an event to when you receive it.
    Std deviation of 28.13ms shows network variability.
============================================================
```

**What this means:**
- Events take **~42ms** to reach you from Polymarket (median)
- Network variance causes **±28ms** of jitter
- 95% of events arrive within **95ms**
- From a VPS in Amsterdam to Polymarket servers

### Mode 2: Local/WSL2 with Calibration

```bash
$ python polymarket_latency.py btc-updown-15m-1769050800 100 10
```

```
First event received! Type: price_change
  Raw latency: -961.23ms
  Calibrating clock offset using first 10 events...

✓ Calibration complete!
  Estimated clock offset: -961.02ms
  Collecting remaining events with offset correction...

Received 20/100 events | Type: price_change | Adjusted latency: 12.45ms
...

============================================================
LATENCY STATISTICS
============================================================

RAW MEASUREMENTS (before calibration):
  Total events: 100
  Median: -961.02ms  ← Clock was 961ms behind
  Mean: -955.18ms
  Std deviation: 28.50ms

────────────────────────────────────────────────────────────
ADJUSTED MEASUREMENTS (clock offset removed):
  Clock offset applied: -961.02ms
  Events used: 90 (after calibration)

  Median latency: 13.25ms
  Mean latency: 14.12ms
  Std deviation: 28.45ms

  Interpretation:
    Median latency of 13.25ms represents the typical time
    from when Polymarket creates an event to when you receive it.
    Std deviation of 28.45ms shows network variability.
============================================================
```

**What this means:**
- Local clock was **961ms behind** Polymarket's clock
- After calibration: **~13ms** estimated network latency
- Calibration is less accurate than NTP sync (for reference only)

## Actual Performance Data

Based on real-world testing from an NTP-synced VPS in Amsterdam (DigitalOcean):

| Metric | Value | Notes |
|--------|-------|-------|
| **Median Latency** | 40-42ms | Typical event delivery time |
| **Mean Latency** | 46-57ms | Average (affected by outliers) |
| **Std Deviation** | 28-48ms | Network jitter/variance |
| **95th Percentile** | ~95ms | 95% of events arrive within this time |
| **Min Latency** | 8-10ms | Best-case delivery |
| **Max Latency** | 130-202ms | Worst-case (during high load/network issues) |

**Test parameters:** 500 events per measurement, multiple markets tested

### Geographic Impact

Latency will vary based on your VPS location relative to Polymarket's servers:
- **US East Coast:** ~10-30ms (closest)
- **Europe (Amsterdam):** ~40-50ms (tested)
- **Asia Pacific:** ~150-250ms (estimated)

### Different Event Types

All event types show similar latency:
- `price_change`: Most common, ~42ms median
- `book`: Order book updates, ~42ms median
- `last_trade_price`: Trade executions, ~42ms median

## Finding Market Slugs

You can find market slugs in Polymarket URLs:
- URL: `https://polymarket.com/event/btc-updown-15m-1769050800`
- Slug: `btc-updown-15m-1769050800`

## Quick Reference

### Production VPS (Recommended - Most Accurate)
```bash
# ONE-TIME SETUP
sudo ./sync-clock.sh  # Enable continuous sync when prompted
timedatectl status    # Verify: "synchronized: yes"

# ONGOING MEASUREMENTS
python polymarket_latency.py btc-updown-15m-1769050800 500 0
# Args: market-slug, 500 events, 0 = no calibration

# EXPECTED RESULTS (Europe)
# Median: ~40-50ms
# Std Dev: ~25-50ms
# All values positive
```

### Local Development / WSL2 (Less Accurate)
```bash
# Run with automatic clock offset calibration
python polymarket_latency.py btc-updown-15m-1769050800 100 10
# Args: market-slug, 100 events, 10 = calibrate with first 10

# EXPECTED RESULTS
# Raw median: may be negative (clock offset)
# Adjusted median: ~10-50ms (estimated)
```

### Troubleshooting

**Seeing negative latencies on VPS?**
```bash
# Check sync status
timedatectl status  # Should show "synchronized: yes"

# Check time offset
ntpdate -q pool.ntp.org  # Should be < 0.05 seconds

# Force resync
sudo ./sync-clock.sh
```

**High latency or variance?**
```bash
# Test multiple times to confirm
for i in {1..3}; do
  python polymarket_latency.py <market-slug> 200 0
  sleep 60
done

# If consistently high:
# - Check VPS network quality
# - Try different geographic region
# - Check Polymarket status page
```

**Very high variance (std dev > 80ms)?**
```bash
# Run in verbose mode to see batching pattern
python polymarket_latency.py <market-slug> 500 0 --verbose

# Look for:
# - Clusters of high latencies (200-300ms)
# - Then clusters of low latencies (5-20ms)
# - Large timestamp gaps between events
# This indicates server-side batching, not network issues
```

**Getting warnings about high variance?**
```
⚠️  High Variance Detected:
    Std deviation (84.35ms) is 2.5x the median.
    This suggests server-side batching/queueing, not just network jitter.
```

This is **normal** - it means Polymarket is batching events internally. Your network is fine. The median latency is still the most reliable metric for typical delivery time.

## API References

- [Polymarket WebSocket Documentation](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [Market Channel Documentation](https://docs.polymarket.com/developers/CLOB/websocket/market-channel)
- [Get Market by Slug API](https://docs.polymarket.com/api-reference/markets/get-market-by-slug)

## Clock Offset Calibration

The tool automatically handles clock synchronization differences between your local system and Polymarket's servers:

1. **Calibration Phase**: The first 10 events (configurable) are used to estimate the clock offset
2. **Offset Calculation**: The median latency from calibration events is used as the clock offset
3. **Adjustment**: All subsequent measurements have the offset removed to show true network latency

### Why This Matters

Without clock synchronization:
- Raw latency might be negative (your clock is behind)
- Raw latency might be inflated (your clock is ahead)

With calibration:
- Adjusted latency shows actual message transmission time
- Standard deviation reflects real network variability

### When Calibration Is Reliable

Clock offset calibration is reliable when:
- ✓ The actual network latency is much smaller than the clock offset
- ✓ Both clocks are stable (not drifting rapidly)
- ✓ You collect enough calibration events (10+ recommended)
- ✓ The measurement period is short (minutes, not hours)

## Best Practices & Recommendations

### ✓ Recommended: NTP-Synced VPS (Most Accurate)

**When to use:**
- Production latency monitoring
- Trading/arbitrage systems
- Accurate absolute measurements
- Multi-server comparisons

**Setup:**
```bash
# One-time: Enable NTP sync
sudo ./sync-clock.sh
# Answer 'y' when prompted for continuous sync

# Verify sync
timedatectl status  # Should show "synchronized: yes"

# Run measurements (calibration disabled)
python polymarket_latency.py <market-slug> 500 0
```

**Expected results:**
- ✓ Positive latencies (30-100ms typical)
- ✓ Median shows true network latency
- ✓ Std deviation shows real network variance
- ✗ Negative latencies = clock sync failed

**Why this is best:**
- Both clocks (yours and Polymarket's) synced to same NTP reference
- No assumptions or calibration needed
- Measurements are reproducible and comparable
- Accurate to within ±1-50ms (NTP accuracy)

### ⚠ Alternative: Calibration (Less Accurate)

**When to use:**
- WSL2/local development (clock drifts)
- No sudo access for NTP
- Quick one-off testing
- Clock offset >> network latency

**Setup:**
```bash
# Run with calibration (default)
python polymarket_latency.py <market-slug> 100 10
```

**Limitations:**
- ⚠ Assumes median of first N events = clock offset
- ⚠ Only works if clock offset >> network latency
- ⚠ Less accurate than NTP (reference only)
- ⚠ Not suitable for sub-50ms precision

**Why calibration is less reliable:**
```
If true latency = 40ms and clock offset = 50ms:
  Calibration will estimate offset as ~90ms (40+50)
  All subsequent measurements will be wrong by ~40ms
```

### 🚫 Don't Do This

**DON'T use calibration with NTP sync:**
```bash
# WRONG - will remove real network variance
sudo ./sync-clock.sh
python polymarket_latency.py <market-slug> 500 10  # ✗ Don't calibrate!
```

**DON'T trust absolute values without NTP:**
```bash
# On local machine without NTP
python polymarket_latency.py <market-slug> 100 0  # ✗ May show negative latencies
```

### Interpreting VPS Results (NTP-Synced)

When running on an NTP-synced VPS with calibration disabled, here's what the numbers mean:

**✓ Healthy Results (Clock Properly Synced):**
```
Median latency: 42.32ms  ← Positive value = clocks synced
Mean latency: 46.01ms    ← Close to median = consistent
Std deviation: 28.13ms   ← Network jitter/variance
Min: 9.69ms              ← Best-case latency
Max: 132.23ms            ← Worst-case (spikes)
95th percentile: 95ms    ← 95% arrive within this time
```

**Interpretation:**
- **Median (42ms)**: Typical time from event creation to receipt
- **Std Dev (28ms)**: Network is variable but stable
- **Range (9-132ms)**: Network conditions vary, but no major issues
- **95th %ile (95ms)**: Good for SLA planning

**✗ Problem Results (Clock Not Synced):**
```
Median latency: -51.56ms  ← NEGATIVE = CLOCK PROBLEM!
Mean latency: -46.38ms    ← Confirms clock offset
```

**Troubleshooting negative latencies:**
```bash
# Check NTP sync status
timedatectl status
# Should show: "System clock synchronized: yes"

# Query actual time difference
ntpdate -q pool.ntp.org
# Should show: offset < 0.05 seconds

# Force resync if needed
sudo ./sync-clock.sh
```

### What Affects Latency?

Based on testing, these factors impact latency:

1. **Geographic Distance** (biggest factor)
   - Same region as Polymarket: 10-30ms
   - Cross-Atlantic: 40-60ms
   - Cross-Pacific: 150-250ms

2. **Network Route Quality**
   - Premium networks (AWS, GCP): Lower variance
   - Budget VPS: Higher variance
   - Observed: 28-48ms std deviation (normal)
   - Observed: 80-100ms std deviation (indicates batching)

3. **Server-Side Batching/Queueing** (significant impact!)
   - **Most common cause of high variance**
   - Events timestamped at creation but queued before sending
   - Results in clusters of high latency (200-300ms) events
   - Then clusters of low latency (5-20ms) events
   - Creates bimodal distribution instead of normal distribution

4. **Market Activity**
   - Low activity: More consistent (~28ms std dev)
   - High activity: More variance (~48-80ms std dev)
   - Spikes during major events (100-300ms possible)

5. **Event Type** (minimal impact)
   - All types show similar latency
   - No significant difference observed

### Understanding High Variance

**Normal Network Variance:**
```
Std dev: 25-50ms
95th percentile: 2-3x median
Max latency: 3-4x median
Pattern: Random fluctuations
```

**Server-Side Batching/Queueing:**
```
Std dev: 80-100ms+
95th percentile: 5-8x median
Max latency: 10x+ median
Pattern: Clusters of high/low latencies
```

**Example of Batching Pattern:**
```
Events 200-250: ALL 225-309ms (queued batch released)
Events 260-420: ALL 4-50ms    (fresh events)
Events 430-470: Rising 30-90ms (queue building up)
```

This is **normal Polymarket behavior**, not a network problem. Use the `--verbose` flag to see timestamp gaps and detect batching.

## Key Takeaways

### For Production Use

1. **Use NTP-synced VPS with calibration disabled (`0`)**
   - Most accurate and reliable measurements
   - Reproducible across different servers
   - No assumptions or corrections needed

2. **Expect ~40-60ms median latency from Europe**
   - Actual value depends on your location
   - Lower from US East Coast (~10-30ms)
   - Higher from Asia Pacific (~150-250ms)

3. **Network variance is normal**
   - Std deviation of 25-50ms is typical
   - 95th percentile 2-3x median is expected
   - Occasional spikes to 100-200ms during peak activity

4. **Monitor continuously for changes**
   - Run every 15-30 minutes to detect issues
   - Compare median over time (should be stable)
   - Alert if median increases >50% or std dev doubles

### For Development/Testing

1. **Calibration works for local/WSL2**
   - Good enough for relative comparisons
   - Don't trust absolute values
   - Re-calibrate periodically

2. **Negative raw latencies = clock offset**
   - Normal on unsynchronized systems
   - Calibration will correct it
   - For accurate values, use NTP instead

### Limitations

1. **One-way latency assumption**
   - Assumes Polymarket uses NTP (likely, but not guaranteed)
   - Accuracy depends on both clocks being synced
   - True accuracy is ±1-50ms (NTP precision)

2. **Application-layer delays unknown**
   - Measures time from event timestamp to receipt
   - Doesn't account for Polymarket's internal delays
   - Actual "freshness" may be slightly worse

3. **Network path can change**
   - Routing changes affect latency
   - Measurements valid for current network conditions
   - Monitor over time to detect changes

## Technical Notes

- All timestamps are in milliseconds (Unix epoch)
- Raw latency: `local_receipt_time - event_timestamp`
- Adjusted latency: `raw_latency - clock_offset`
- Connection auto-closes after collecting specified events
- Non-timestamped messages (e.g., subscription confirmations) are excluded from statistics
- PING messages sent every 10 seconds to maintain WebSocket connection
