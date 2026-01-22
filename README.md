# Polymarket WebSocket Latency Tracker

A Python tool to measure latency between Polymarket websocket events and local receipt time.

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

### Example

```bash
python polymarket_latency.py btc-updown-15m-1769050800 20
```

Or with default 100 events:
```bash
python polymarket_latency.py will-bitcoin-hit-100k-in-2024
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

## Output

The tool provides real-time progress updates and final statistics:

```
Fetching market info for slug: btc-updown-15m-1769050800
Market: Bitcoin Up or Down - January 21, 10:00PM-10:15PM ET
Token IDs: ['104764...', '71470...']

Connecting to WebSocket...
Collecting 100 events...

First event received! Type: price_change
  Raw latency: -961.23ms
  Calibrating clock offset using first 10 events...

✓ Calibration complete!
  Estimated clock offset: -961.02ms
  Collecting remaining events with offset correction...

Received 20/100 events | Type: price_change | Adjusted latency: 12.45ms
Received 30/100 events | Type: price_change | Adjusted latency: 15.32ms
...

============================================================
LATENCY STATISTICS
============================================================

RAW MEASUREMENTS (with clock offset):
  Total events: 100
  Median: -961.02ms
  Mean: -955.18ms
  Min: -983.62ms
  Max: -854.47ms
  Std deviation: 28.50ms

────────────────────────────────────────────────────────────
ADJUSTED MEASUREMENTS (clock offset removed):
  Clock offset applied: -961.02ms
  Events used: 90 (after calibration)

  Median latency: 13.25ms
  Mean latency: 14.12ms
  Min latency: 8.50ms
  Max latency: 22.30ms
  Std deviation: 3.45ms

  Percentiles:
    25th: 11.20ms
    75th: 16.50ms
    95th: 20.10ms
    99th: 21.80ms

  Interpretation:
    Median latency of 13.25ms represents the typical time
    from when Polymarket creates an event to when you receive it.
    Std deviation of 3.45ms shows network variability.
============================================================
```

## Finding Market Slugs

You can find market slugs in Polymarket URLs:
- URL: `https://polymarket.com/event/will-bitcoin-hit-100k-in-2024`
- Slug: `will-bitcoin-hit-100k-in-2024`

## Quick Reference

### Local Development / WSL2 (with calibration)
```bash
# Run with automatic clock offset calibration
python polymarket_latency.py btc-updown-15m-1769050800 100 10
```

### Production VPS (with NTP sync)
```bash
# One-time setup
sudo ./sync-clock.sh  # Enable continuous sync when prompted

# Run measurements (no calibration needed)
python polymarket_latency.py btc-updown-15m-1769050800 100 0
```

### Check Clock Sync Status
```bash
timedatectl status
ntpdate -q pool.ntp.org  # Query time difference without syncing
```

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

### Calibration vs NTP Sync: Which to Use?

**Use Calibration (default):**
- Quick testing on local machines
- WSL2 environments (clock can drift)
- You don't have sudo access
- Short measurement sessions

**Use NTP Sync (`sync-clock.sh`):**
- Production VPS monitoring
- Long-running measurements
- Comparing data across multiple servers
- You need absolute timestamp accuracy
- Building trading/arbitrage systems

**Best Practice for VPS:**
```bash
# Initial setup - enable continuous sync
sudo ./sync-clock.sh
# Answer 'y' when prompted for continuous sync

# Then run measurements anytime without calibration
python polymarket_latency.py <market-slug> 100 0  # 0 = no calibration needed
```

## Notes

- All timestamps are in milliseconds (Unix epoch)
- Raw latency is calculated as: `local_receipt_time - event_timestamp`
- Adjusted latency is: `raw_latency - clock_offset`
- The tool automatically closes the connection after collecting the specified number of events
- Some messages (like subscription confirmations) may not have timestamps and are excluded from latency calculations
