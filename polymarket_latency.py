#!/usr/bin/env python3
"""
Polymarket WebSocket Latency Measurement Tool

Connects to Polymarket's websocket, subscribes to a market by slug,
collects 100 events, and calculates the median latency between
event timestamps and local receipt time.

Supports both Market channel (public) and User channel (authenticated).
"""

import json
import os
import time
import statistics
import threading
from typing import List, Dict, Any, Optional
import requests
from websocket import WebSocketApp

# Load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on environment variables

# Channel types
MARKET_CHANNEL = "market"
USER_CHANNEL = "user"

# WebSocket base URL
WS_BASE_URL = "wss://ws-subscriptions-clob.polymarket.com"


class PolymarketLatencyTracker:
    def __init__(self, market_slug: str, num_events: int = 100, calibration_events: int = 10, verbose: bool = False):
        self.market_slug = market_slug
        self.num_events = num_events
        self.calibration_events = min(calibration_events, num_events // 2)  # At most half the events
        self.verbose = verbose
        self.ws_url = f"{WS_BASE_URL}/ws/{MARKET_CHANNEL}"
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


class UserEventsTracker:
    """
    Connects to Polymarket's User channel WebSocket for authenticated
    updates on orders and trades.

    Requires API credentials (api_key, api_secret, api_passphrase).
    Credentials can be passed directly or loaded from environment variables:
      - POLY_API_KEY
      - POLY_API_SECRET
      - POLY_API_PASSPHRASE
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        markets: Optional[List[str]] = None,
        verbose: bool = False,
        message_callback: Optional[callable] = None,
    ):
        # Load credentials from params or environment
        self.api_key = api_key or os.environ.get("POLY_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("POLY_API_SECRET", "")
        self.api_passphrase = api_passphrase or os.environ.get("POLY_API_PASSPHRASE", "")

        self.markets = markets or []  # Condition IDs to filter (empty = all)
        self.verbose = verbose
        self.message_callback = message_callback

        self.ws_url = f"{WS_BASE_URL}/ws/{USER_CHANNEL}"
        self.ws: WebSocketApp = None
        self.ping_thread = None

        # Event tracking
        self.events_received = 0
        self.trades: List[Dict[str, Any]] = []
        self.orders: List[Dict[str, Any]] = []
        self.raw_latencies: List[float] = []

    def _validate_credentials(self) -> bool:
        """Check if all required credentials are provided."""
        if not self.api_key or not self.api_secret or not self.api_passphrase:
            print("Error: Missing API credentials.")
            print("Provide credentials via arguments or environment variables:")
            print("  - POLY_API_KEY")
            print("  - POLY_API_SECRET")
            print("  - POLY_API_PASSPHRASE")
            return False
        return True

    def on_message(self, ws, message):
        """Handle incoming websocket messages."""
        receive_time_ms = time.time() * 1000

        # Handle PING/PONG
        if message.strip() in ["PING", "PONG"]:
            if self.verbose:
                print(f"Received: {message.strip()}")
            return

        try:
            data = json.loads(message)

            # Handle array messages
            if isinstance(data, list):
                for item in data:
                    self._process_event(item, receive_time_ms)
                return

            if isinstance(data, dict):
                self._process_event(data, receive_time_ms)
            else:
                print(f"Received unexpected message type: {type(data)}")

        except json.JSONDecodeError:
            print(f"Failed to parse message: {message[:100]}...")
        except Exception as e:
            print(f"Error processing message: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()

    def _process_event(self, data: Dict[str, Any], receive_time_ms: float):
        """Process a single event from the websocket."""
        # Check for error responses
        if data.get("error") or data.get("message"):
            print(f"Server message: {data}")
            return

        event_type = data.get("event_type", data.get("type", "unknown"))
        self.events_received += 1

        # Extract timestamp and calculate latency
        event_timestamp = data.get("timestamp")
        latency_ms = None
        if event_timestamp:
            if isinstance(event_timestamp, str):
                event_timestamp = float(event_timestamp)
            latency_ms = receive_time_ms - event_timestamp
            self.raw_latencies.append(latency_ms)

        # Categorize event
        if event_type == "trade" or data.get("type") == "TRADE":
            self.trades.append(data)
            self._print_trade_event(data, latency_ms)
        elif event_type == "order" or data.get("type") in ["PLACEMENT", "UPDATE", "CANCELLATION"]:
            self.orders.append(data)
            self._print_order_event(data, latency_ms)
        else:
            if self.verbose:
                print(f"Unknown event type: {event_type}")
                print(f"  Data: {json.dumps(data, indent=2)[:500]}")

        # Call user callback if provided
        if self.message_callback:
            self.message_callback(data, latency_ms)

    def _print_trade_event(self, data: Dict[str, Any], latency_ms: Optional[float]):
        """Print formatted trade event."""
        status = data.get("status", "")
        side = data.get("side", "")
        size = data.get("size", "")
        price = data.get("price", "")
        outcome = data.get("outcome", "")
        trade_id = data.get("id", "")[:16] + "..." if data.get("id") else ""

        latency_str = f" | Latency: {latency_ms:.0f}ms" if latency_ms else ""

        print(f"TRADE #{self.events_received}: {status} {side} {size}@{price} {outcome} [{trade_id}]{latency_str}")

        if self.verbose:
            print(f"  Market: {data.get('market', 'N/A')}")
            print(f"  Taker Order: {data.get('taker_order_id', 'N/A')[:32]}...")
            maker_orders = data.get("maker_orders", [])
            if maker_orders:
                print(f"  Maker Orders: {len(maker_orders)}")

    def _print_order_event(self, data: Dict[str, Any], latency_ms: Optional[float]):
        """Print formatted order event."""
        event_subtype = data.get("type", "")
        side = data.get("side", "")
        original_size = data.get("original_size", "")
        size_matched = data.get("size_matched", "")
        price = data.get("price", "")
        outcome = data.get("outcome", "")
        order_id = data.get("id", "")[:16] + "..." if data.get("id") else ""

        latency_str = f" | Latency: {latency_ms:.0f}ms" if latency_ms else ""

        print(f"ORDER #{self.events_received}: {event_subtype} {side} {original_size}@{price} {outcome} (matched: {size_matched}) [{order_id}]{latency_str}")

        if self.verbose:
            print(f"  Market: {data.get('market', 'N/A')}")
            print(f"  Owner: {data.get('owner', 'N/A')}")

    def on_error(self, ws, error):
        """Handle websocket errors."""
        print(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """Handle websocket connection close."""
        print(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._display_summary()

    def ping(self, ws):
        """Send periodic PING messages to keep connection alive."""
        while ws.keep_running:
            try:
                ws.send("PING")
                time.sleep(10)
            except Exception:
                break

    def on_open(self, ws):
        """Handle websocket connection open and send authentication."""
        print(f"WebSocket connected. Authenticating...")

        # Build auth object
        auth = {
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "passphrase": self.api_passphrase,
        }

        # Subscription message for user channel (markets field is always required)
        subscription_message = {
            "markets": self.markets,  # Empty list = all markets
            "type": USER_CHANNEL,
            "auth": auth,
        }

        ws.send(json.dumps(subscription_message))
        print(f"Authentication sent (API key: {self.api_key[:8]}...). Listening for user events...")
        if self.markets:
            print(f"Filtering by markets: {self.markets}")
        else:
            print("Receiving events for all markets.")

        # Start ping thread
        self.ping_thread = threading.Thread(target=self.ping, args=(ws,))
        self.ping_thread.daemon = True
        self.ping_thread.start()

    def _display_summary(self):
        """Display summary of received events."""
        print(f"\n{'='*60}")
        print("USER EVENTS SUMMARY")
        print(f"{'='*60}")
        print(f"Total events received: {self.events_received}")
        print(f"  Trades: {len(self.trades)}")
        print(f"  Orders: {len(self.orders)}")

        if self.raw_latencies:
            median_latency = statistics.median(self.raw_latencies)
            mean_latency = statistics.mean(self.raw_latencies)
            print(f"\nLatency Statistics:")
            print(f"  Median: {median_latency:.2f}ms")
            print(f"  Mean: {mean_latency:.2f}ms")
            print(f"  Min: {min(self.raw_latencies):.2f}ms")
            print(f"  Max: {max(self.raw_latencies):.2f}ms")

        print(f"{'='*60}")

    def run(self):
        """Start the WebSocket connection."""
        if not self._validate_credentials():
            return

        print(f"\nConnecting to User Events WebSocket: {self.ws_url}")
        print("Press Ctrl+C to stop.\n")

        self.ws = WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )

        try:
            self.ws.run_forever()
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            self.ws.close()


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  Market channel (public):")
        print("    python polymarket_latency.py market <market-slug> [num_events] [calibration_events] [--verbose]")
        print("")
        print("  User channel (authenticated):")
        print("    python polymarket_latency.py user [--verbose]")
        print("    python polymarket_latency.py user --markets <condition_id1,condition_id2> [--verbose]")
        print("")
        print("Arguments:")
        print("  market-slug         : Polymarket market slug (for market channel)")
        print("  num_events          : Total events to collect (default: 100)")
        print("  calibration_events  : Events for clock offset calibration (default: 10)")
        print("  --markets           : Comma-separated condition IDs to filter (user channel)")
        print("  --verbose, -v       : Show detailed output")
        print("")
        print("Environment variables for user channel:")
        print("  POLY_API_KEY        : Your Polymarket API key")
        print("  POLY_API_SECRET     : Your Polymarket API secret")
        print("  POLY_API_PASSPHRASE : Your Polymarket API passphrase")
        print("")
        print("Examples:")
        print("  # Market channel - measure latency for a specific market")
        print("  python polymarket_latency.py market btc-updown-15m-1769050800 500 0")
        print("")
        print("  # User channel - listen for your order/trade events")
        print("  export POLY_API_KEY=your_key")
        print("  export POLY_API_SECRET=your_secret")
        print("  export POLY_API_PASSPHRASE=your_passphrase")
        print("  python polymarket_latency.py user --verbose")
        sys.exit(1)

    # Check for verbose flag
    verbose = '--verbose' in sys.argv or '-v' in sys.argv

    # Parse --markets flag
    markets = []
    markets_value = None
    if '--markets' in sys.argv:
        markets_idx = sys.argv.index('--markets')
        if markets_idx + 1 < len(sys.argv):
            markets_value = sys.argv[markets_idx + 1]
            markets = [m.strip() for m in markets_value.split(',') if m.strip()]

    # Filter out flags from args
    skip_next = False
    args = []
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in ['--verbose', '-v']:
            continue
        if arg == '--markets':
            skip_next = True
            continue
        args.append(arg)

    if not args:
        print("Error: Please specify 'market' or 'user' as the first argument.")
        sys.exit(1)

    channel = args[0].lower()

    if channel == "user":
        # User channel mode
        tracker = UserEventsTracker(
            markets=markets if markets else None,
            verbose=verbose,
        )
        tracker.run()

    elif channel == "market":
        # Market channel mode (original behavior)
        if len(args) < 2:
            print("Error: market-slug is required for market channel.")
            print("Usage: python polymarket_latency.py market <market-slug> [num_events] [calibration_events]")
            sys.exit(1)

        market_slug = args[1]
        num_events = int(args[2]) if len(args) > 2 else 100
        calibration_events = int(args[3]) if len(args) > 3 else 10

        tracker = PolymarketLatencyTracker(market_slug, num_events, calibration_events, verbose)
        tracker.run()

    else:
        # Backward compatibility: treat first arg as market slug
        market_slug = args[0]
        num_events = int(args[1]) if len(args) > 1 else 100
        calibration_events = int(args[2]) if len(args) > 2 else 10

        tracker = PolymarketLatencyTracker(market_slug, num_events, calibration_events, verbose)
        tracker.run()


if __name__ == "__main__":
    main()
