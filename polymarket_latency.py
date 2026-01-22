#!/usr/bin/env python3
"""
Polymarket WebSocket Latency Measurement Tool

Connects to Polymarket's websocket, subscribes to a market by slug,
collects 100 events, and calculates the median latency between
event timestamps and local receipt time.
"""

import json
import time
import statistics
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any
import requests
from websocket import WebSocketApp


class PolymarketLatencyTracker:
    def __init__(self, market_slug: str, num_events: int = 100, calibration_events: int = 10, verbose: bool = False):
        self.market_slug = market_slug
        self.num_events = num_events
        self.calibration_events = min(calibration_events, num_events // 2)  # At most half the events
        self.verbose = verbose
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.api_url = f"https://gamma-api.polymarket.com/markets/slug/{market_slug}"

        self.raw_latencies: List[float] = []  # Raw latencies (with clock offset)
        self.adjusted_latencies: List[float] = []  # Adjusted latencies (offset removed)
        self.event_timestamps: List[float] = []  # Event timestamps for analysis
        self.receive_timestamps: List[float] = []  # Receive timestamps for analysis
        self.events_received = 0
        self.token_ids: List[str] = []
        self.ws: WebSocketApp = None
        self.ping_thread = None
        self.clock_offset: float = None  # Estimated clock offset in ms
        self.calibration_complete = False

    def fetch_market_info(self) -> Dict[str, Any]:
        """Fetch market information from Polymarket REST API to get token IDs."""
        print(f"Fetching market info for slug: {self.market_slug}")
        response = requests.get(self.api_url)
        response.raise_for_status()
        market_data = response.json()

        print(f"Market: {market_data.get('question', 'N/A')}")
        print(f"Condition ID: {market_data.get('conditionId', 'N/A')}")

        # clobTokenIds can be a JSON-encoded array or a comma-separated string
        clob_token_ids = market_data.get('clobTokenIds', '')
        if isinstance(clob_token_ids, str):
            # Try parsing as JSON first
            try:
                self.token_ids = json.loads(clob_token_ids)
            except (json.JSONDecodeError, ValueError):
                # Fall back to comma-separated parsing
                self.token_ids = [tid.strip() for tid in clob_token_ids.split(',') if tid.strip()]
        elif isinstance(clob_token_ids, list):
            self.token_ids = clob_token_ids
        else:
            self.token_ids = []

        print(f"Token IDs: {self.token_ids}")
        return market_data

    def on_message(self, ws, message):
        """Handle incoming websocket messages."""
        receive_time_ms = time.time() * 1000  # Local receipt time in milliseconds

        try:
            data = json.loads(message)

            # Handle both list and dict responses
            if isinstance(data, list):
                # Some initial messages might be arrays, skip them
                print(f"Received array message (length: {len(data)}), skipping...")
                return

            if not isinstance(data, dict):
                print(f"Received non-dict, non-list message: {type(data)}")
                return

            # Extract timestamp from the event (if available)
            event_timestamp = data.get('timestamp')
            event_type = data.get('event_type', 'unknown')

            if event_timestamp:
                # Convert event timestamp to float if it's a string
                if isinstance(event_timestamp, str):
                    event_timestamp = float(event_timestamp)

                # Calculate raw latency in milliseconds
                raw_latency_ms = receive_time_ms - event_timestamp
                self.raw_latencies.append(raw_latency_ms)
                self.event_timestamps.append(event_timestamp)
                self.receive_timestamps.append(receive_time_ms)
                self.events_received += 1

                # Calibration phase: collect first N events to estimate clock offset
                if not self.calibration_complete and self.calibration_events > 0:
                    if self.events_received == 1:
                        print(f"First event received! Type: {event_type}")
                        print(f"  Raw latency: {raw_latency_ms:.2f}ms")
                        print(f"  Calibrating clock offset using first {self.calibration_events} events...")

                    if self.events_received >= self.calibration_events:
                        # Calculate clock offset as median of raw latencies
                        self.clock_offset = statistics.median(self.raw_latencies[:self.calibration_events])
                        self.calibration_complete = True
                        print(f"\n✓ Calibration complete!")
                        print(f"  Estimated clock offset: {self.clock_offset:.2f}ms")
                        print(f"  Collecting remaining events with offset correction...\n")

                # If calibration is disabled (calibration_events == 0), mark as complete immediately
                elif not self.calibration_complete and self.calibration_events == 0:
                    if self.events_received == 1:
                        print(f"First event received! Type: {event_type}")
                        print(f"  Raw latency: {raw_latency_ms:.2f}ms")
                        print(f"  Clock calibration DISABLED - using raw measurements only\n")
                    self.calibration_complete = True  # Skip calibration entirely

                # Apply clock offset correction (only if calibration was done)
                if self.calibration_complete and self.clock_offset is not None:
                    adjusted_latency_ms = raw_latency_ms - self.clock_offset
                    self.adjusted_latencies.append(adjusted_latency_ms)

                    # Print progress every 10 events (after calibration)
                    if (self.events_received - self.calibration_events) % 10 == 0 and self.events_received > self.calibration_events:
                        print(f"Received {self.events_received}/{self.num_events} events | "
                              f"Type: {event_type} | Adjusted latency: {adjusted_latency_ms:.2f}ms")

                # Print progress for raw measurements when calibration is disabled
                elif self.calibration_complete and self.clock_offset is None:
                    if self.events_received % 10 == 0 or self.verbose:
                        output = f"Received {self.events_received}/{self.num_events} events | Type: {event_type} | Raw latency: {raw_latency_ms:.2f}ms"
                        if self.verbose:
                            # Calculate time since last event
                            if len(self.event_timestamps) > 1:
                                time_since_last = event_timestamp - self.event_timestamps[-2]
                                output += f" | Gap: {time_since_last:.0f}ms"
                        print(output)

                # Close connection after collecting enough events
                if self.events_received >= self.num_events:
                    print(f"\nCollected {self.num_events} events. Closing connection...")
                    ws.close()
            else:
                # Some messages might not have timestamps (e.g., subscription confirmations)
                print(f"Received message without timestamp: {event_type}")

        except json.JSONDecodeError:
            # Might be a PING/PONG message
            if message.strip() not in ["PING", "PONG"]:
                print(f"Failed to parse message: {message}")
        except Exception as e:
            print(f"Error processing message: {e}")
            import traceback
            traceback.print_exc()

    def on_error(self, ws, error):
        """Handle websocket errors."""
        print(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """Handle websocket connection close."""
        print(f"WebSocket closed: {close_status_code} - {close_msg}")

    def ping(self, ws):
        """Send periodic PING messages to keep connection alive."""
        while ws.keep_running:
            try:
                ws.send("PING")
                time.sleep(10)
            except Exception:
                break

    def on_open(self, ws):
        """Handle websocket connection open and send subscription message."""
        print(f"WebSocket connected. Subscribing to market...")

        # Subscribe to the market channel with the token IDs
        subscription_message = {
            "assets_ids": self.token_ids,
            "type": "market"
        }

        ws.send(json.dumps(subscription_message))
        print(f"Subscription message sent: {subscription_message}")

        # Start ping thread to keep connection alive
        self.ping_thread = threading.Thread(target=self.ping, args=(ws,))
        self.ping_thread.daemon = True
        self.ping_thread.start()

    def run(self):
        """Main execution flow."""
        # Step 1: Fetch market information to get token IDs
        try:
            self.fetch_market_info()
        except requests.exceptions.RequestException as e:
            print(f"Failed to fetch market info: {e}")
            return

        if not self.token_ids:
            print("No token IDs found for this market. Cannot proceed.")
            return

        # Step 2: Connect to WebSocket and collect events
        print(f"\nConnecting to WebSocket: {self.ws_url}")
        print(f"Collecting {self.num_events} events...\n")

        self.ws = WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

        # Run the WebSocket connection (this blocks until closed)
        self.ws.run_forever()

        # Step 3: Calculate and display latency statistics
        self.display_results()

    def display_results(self):
        """Calculate and display latency statistics."""
        if not self.raw_latencies:
            print("\nNo latency data collected.")
            return

        print(f"\n{'='*60}")
        print("LATENCY STATISTICS")
        print(f"{'='*60}")

        # Display raw statistics
        raw_median = statistics.median(self.raw_latencies)
        raw_mean = statistics.mean(self.raw_latencies)
        raw_stdev = statistics.stdev(self.raw_latencies) if len(self.raw_latencies) > 1 else 0

        # If calibration was disabled, show raw as the primary measurement
        if self.clock_offset is None:
            print(f"\nLATENCY MEASUREMENTS (NTP-synced, no calibration):")
            print(f"  Total events: {len(self.raw_latencies)}")
            print(f"  Median latency: {raw_median:.2f}ms")
            print(f"  Mean latency: {raw_mean:.2f}ms")
            print(f"  Min latency: {min(self.raw_latencies):.2f}ms")
            print(f"  Max latency: {max(self.raw_latencies):.2f}ms")
            if len(self.raw_latencies) > 1:
                print(f"  Std deviation: {raw_stdev:.2f}ms")

            # Show percentiles
            sorted_raw = sorted(self.raw_latencies)
            p25 = sorted_raw[len(sorted_raw) // 4]
            p75 = sorted_raw[3 * len(sorted_raw) // 4]
            p95 = sorted_raw[int(0.95 * len(sorted_raw))] if len(sorted_raw) > 1 else sorted_raw[0]
            p99 = sorted_raw[int(0.99 * len(sorted_raw))] if len(sorted_raw) > 1 else sorted_raw[0]

            print(f"\n  Percentiles:")
            print(f"    25th: {p25:.2f}ms")
            print(f"    75th: {p75:.2f}ms")
            print(f"    95th: {p95:.2f}ms")
            print(f"    99th: {p99:.2f}ms")

            print(f"\n  Interpretation:")
            print(f"    Median latency of {raw_median:.2f}ms represents the typical time")
            print(f"    from when Polymarket creates an event to when you receive it.")
            print(f"    Std deviation of {raw_stdev:.2f}ms shows network variability.")

            # Detect potential batching/queueing issues
            if raw_stdev > raw_median * 1.5:
                print(f"\n  ⚠️  High Variance Detected:")
                print(f"    Std deviation ({raw_stdev:.2f}ms) is {raw_stdev/raw_median:.1f}x the median.")
                print(f"    This suggests server-side batching/queueing, not just network jitter.")
                print(f"    Events may be timestamped at creation but queued before sending.")

            # Analyze event timestamp gaps to detect batching
            if len(self.event_timestamps) > 10:
                gaps = [self.event_timestamps[i] - self.event_timestamps[i-1]
                        for i in range(1, len(self.event_timestamps))]
                median_gap = statistics.median(gaps)
                max_gap = max(gaps)
                print(f"\n  Event Timing Analysis:")
                print(f"    Median time between events: {median_gap:.0f}ms")
                print(f"    Max gap between events: {max_gap:.0f}ms")
                if max_gap > median_gap * 10:
                    print(f"    ⚠️  Large gaps detected - events may arrive in bursts")
        else:
            # Show raw as secondary when calibration was used
            print(f"\nRAW MEASUREMENTS (before calibration):")
            print(f"  Total events: {len(self.raw_latencies)}")
            print(f"  Median: {raw_median:.2f}ms")
            print(f"  Mean: {raw_mean:.2f}ms")
            print(f"  Min: {min(self.raw_latencies):.2f}ms")
            print(f"  Max: {max(self.raw_latencies):.2f}ms")
            if len(self.raw_latencies) > 1:
                print(f"  Std deviation: {raw_stdev:.2f}ms")

        # Display adjusted statistics if calibration was completed
        if self.adjusted_latencies and self.clock_offset is not None:
            print(f"\n{'─'*60}")
            print(f"ADJUSTED MEASUREMENTS (clock offset removed):")
            print(f"  Clock offset applied: {self.clock_offset:.2f}ms")
            print(f"  Events used: {len(self.adjusted_latencies)} (after calibration)")

            adj_median = statistics.median(self.adjusted_latencies)
            adj_mean = statistics.mean(self.adjusted_latencies)

            print(f"\n  Median latency: {adj_median:.2f}ms")
            print(f"  Mean latency: {adj_mean:.2f}ms")
            print(f"  Min latency: {min(self.adjusted_latencies):.2f}ms")
            print(f"  Max latency: {max(self.adjusted_latencies):.2f}ms")

            if len(self.adjusted_latencies) > 1:
                adj_stdev = statistics.stdev(self.adjusted_latencies)
                print(f"  Std deviation: {adj_stdev:.2f}ms")

            # Percentiles for adjusted latencies
            sorted_adj = sorted(self.adjusted_latencies)
            p25 = sorted_adj[len(sorted_adj) // 4]
            p75 = sorted_adj[3 * len(sorted_adj) // 4]
            p95 = sorted_adj[int(0.95 * len(sorted_adj))] if len(sorted_adj) > 1 else sorted_adj[0]
            p99 = sorted_adj[int(0.99 * len(sorted_adj))] if len(sorted_adj) > 1 else sorted_adj[0]

            print(f"\n  Percentiles:")
            print(f"    25th: {p25:.2f}ms")
            print(f"    75th: {p75:.2f}ms")
            print(f"    95th: {p95:.2f}ms")
            print(f"    99th: {p99:.2f}ms")

            print(f"\n  Interpretation:")
            print(f"    Median latency of {adj_median:.2f}ms represents the typical time")
            print(f"    from when Polymarket creates an event to when you receive it.")
            if adj_stdev is not None:
                print(f"    Std deviation of {adj_stdev:.2f}ms shows network variability.")

        print(f"{'='*60}")


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python polymarket_latency.py <market-slug> [num_events] [calibration_events] [--verbose]")
        print("\nArguments:")
        print("  market-slug         : Polymarket market slug (required)")
        print("  num_events          : Total events to collect (default: 100)")
        print("  calibration_events  : Events to use for clock offset calibration (default: 10)")
        print("  --verbose, -v       : Show detailed output for each event")
        print("\nExample:")
        print("  python polymarket_latency.py btc-updown-15m-1769050800 500 0")
        print("  python polymarket_latency.py btc-updown-15m-1769050800 100 10 --verbose")
        sys.exit(1)

    # Check for verbose flag
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    args = [arg for arg in sys.argv[1:] if arg not in ['--verbose', '-v']]

    market_slug = args[0]
    num_events = int(args[1]) if len(args) > 1 else 100
    calibration_events = int(args[2]) if len(args) > 2 else 10

    tracker = PolymarketLatencyTracker(market_slug, num_events, calibration_events, verbose)
    tracker.run()


if __name__ == "__main__":
    main()
