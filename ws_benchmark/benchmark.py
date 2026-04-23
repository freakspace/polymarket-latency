#!/usr/bin/env python3
"""
Benchmark Polymarket CLOB market-data WebSocket topologies.

Compares raw websocket groups (for example 1, 2, 5, 10 connections) over the
same wall-clock window and reports relative coverage, first-seen wins, and
freshness.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import ctypes.util
import gc
import hashlib
import html
import json
import math
import os
import random
import resource
import statistics
import sys
import time
from array import array
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import websockets

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older Python fallback
    tomllib = None

try:
    import orjson  # type: ignore

    def _fast_loads(payload: Any) -> Any:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return orjson.loads(payload)

    def _fast_canonical_dumps(value: Any) -> bytes:
        # orjson with SORT_KEYS produces canonical UTF-8 bytes — same dedup key
        # as json.dumps(sort_keys=True, separators=...) but ~5× faster.
        return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)

    _HAS_ORJSON = True
except ModuleNotFoundError:
    orjson = None  # type: ignore[assignment]

    def _fast_loads(payload: Any) -> Any:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    def _fast_canonical_dumps(value: Any) -> bytes:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    _HAS_ORJSON = False

try:
    import uvloop  # type: ignore

    _HAS_UVLOOP = True
except ModuleNotFoundError:
    uvloop = None  # type: ignore[assignment]
    _HAS_UVLOOP = False

DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_CONFIG_FILENAME = "benchmark_config.toml"
DEFAULT_EVENT_TYPES = ("book", "price_change", "last_trade_price")
DEFAULT_DISTRIBUTION_SAMPLE_SIZE = 8192
DEFAULT_EVENT_RETENTION_SECONDS = 30.0
CHART_COLORS = (
    "#2563eb",
    "#059669",
    "#dc2626",
    "#7c3aed",
    "#ea580c",
    "#0891b2",
)
LOCAL_DIAGNOSTIC_FIELDS = {
    "asset_ids",
    "market_slug",
    "phase_kind",
    "connection_id",
    "event_key",
    "in_warmup",
    "received_at_iso",
    "received_at_ns",
    "segment_id",
    "series_id",
    "switch_reason",
    "topology_id",
    "topology_size",
    "venue_timestamp_iso",
    "venue_timestamp_ns",
    "venue_timestamp_parse_mode",
    "venue_timestamp_raw",
}


def _warn(message: str) -> None:
    print(f"[warn] {message}", file=sys.stderr)


def status_log(message: str) -> None:
    print(message, flush=True)


async def _fetch_json(url: str) -> Any:
    def _load() -> Any:
        request = Request(
            url,
            headers={
                "accept": "application/json",
                "user-agent": "polymarket-clob-ws-benchmark-standalone/1.0",
            },
        )
        with urlopen(request, timeout=10.0) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)

    try:
        return await asyncio.to_thread(_load)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        _warn(f"Gamma request failed for {url}: HTTP {exc.code}")
        return None
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        _warn(f"Gamma request failed for {url}: {exc}")
        return None


def _parse_json_list(value: Any) -> Optional[list[Any]]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_token_ids(value: Any) -> Optional[list[str]]:
    token_ids = _parse_json_list(value) if isinstance(value, str) else value
    if not isinstance(token_ids, list):
        return None
    parsed = [
        str(token_id).strip()
        for token_id in token_ids
        if str(token_id or "").strip()
    ]
    return parsed if len(parsed) == 2 else None


def _parse_recurrence_seconds(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        seconds = int(value)
        return seconds if seconds > 0 else None
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    suffix_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = raw[-1]
    if unit not in suffix_map:
        return None
    try:
        amount = int(raw[:-1].strip())
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount * suffix_map[unit]


def _extract_active_series_events(series_payload: dict[str, Any]) -> list[dict[str, Any]]:
    now = utc_now()
    events = series_payload.get("events", [])
    if not isinstance(events, list):
        return []
    filtered: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("closed") is not False:
            continue
        end_time = parse_market_datetime(event.get("endDate"))
        if end_time is None or end_time <= now:
            continue
        filtered.append(event)
    filtered.sort(
        key=lambda event: parse_market_datetime(event.get("endDate"))
        or datetime.max.replace(tzinfo=timezone.utc)
    )
    return filtered


def _build_market_configuration(
    details: dict[str, Any],
    *,
    series_id: str = "",
    event_payload: Optional[dict[str, Any]] = None,
    recurrence_seconds: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    token_ids = _parse_token_ids(details.get("clobTokenIds"))
    if not token_ids:
        return None

    event_start_time = (
        details.get("eventStartTime")
        or details.get("startTime")
        or details.get("acceptingOrdersTimestamp")
        or details.get("startDate")
        or (event_payload or {}).get("startDate")
    )
    event_end_time = (
        details.get("closedTime")
        or details.get("umaEndDate")
        or details.get("endDate")
        or (event_payload or {}).get("endDate")
    )
    if not event_start_time and event_end_time and recurrence_seconds:
        parsed_end = parse_market_datetime(event_end_time)
        if parsed_end is not None:
            event_start_time = (parsed_end - timedelta(seconds=recurrence_seconds)).isoformat()

    resolved_status = str(details.get("umaResolutionStatus") or details.get("resolutionStatus") or "").strip().upper()
    is_closed = (
        details.get("closed") is True
        or details.get("active") is False
        or (event_payload or {}).get("closed") is True
        or resolved_status in {"SETTLED", "RESOLVED", "FINALIZED", "CLOSED"}
    )

    resolved_series_id = (
        details.get("seriesId")
        or details.get("series_id")
        or (event_payload or {}).get("seriesId")
        or series_id
    )

    return {
        "slug": details.get("slug"),
        "series_id": str(resolved_series_id or "").strip(),
        "up_token_id": token_ids[0],
        "down_token_id": token_ids[1],
        "start_time": event_start_time,
        "end_time": event_end_time,
        "event_closed": (event_payload or {}).get("closed"),
        "status": "CLOSED" if is_closed else "OPEN",
        "volume_24h": _parse_float(details.get("volume24hr") or details.get("volume24Hour") or details.get("volume24h")),
        "liquidity": _parse_float(details.get("liquidityNum") or details.get("liquidity") or details.get("liquidityUSD")),
    }


def _build_event_market_configurations(
    event_payload: dict[str, Any],
    *,
    include_closed: bool = False,
    series_id: Optional[str] = None,
    recurrence_seconds: Optional[int] = None,
) -> list[dict[str, Any]]:
    markets = event_payload.get("markets")
    if not isinstance(markets, list):
        return []
    results: list[dict[str, Any]] = []
    effective_series_id = str(
        series_id or event_payload.get("seriesId") or ""
    ).strip()
    for market in markets:
        if not isinstance(market, dict):
            continue
        config_row = _build_market_configuration(
            market,
            series_id=effective_series_id,
            event_payload=event_payload,
            recurrence_seconds=recurrence_seconds,
        )
        if config_row is None:
            continue
        if not include_closed and str(config_row.get("status") or "").upper() != "OPEN":
            continue
        results.append(config_row)
    return results


async def _fetch_market_by_slug(slug: str) -> Optional[dict[str, Any]]:
    encoded = quote(slug, safe="")
    payload = await _fetch_json(f"{GAMMA_API_BASE_URL}/markets/slug/{encoded}")
    return payload if isinstance(payload, dict) else None


async def _fetch_event_by_slug(slug: str) -> Optional[dict[str, Any]]:
    encoded = quote(slug, safe="")
    payload = await _fetch_json(f"{GAMMA_API_BASE_URL}/events/slug/{encoded}")
    return payload if isinstance(payload, dict) else None


async def _fetch_event_by_id(event_id: str) -> Optional[dict[str, Any]]:
    encoded = quote(str(event_id), safe="")
    for url in (
        f"{GAMMA_API_BASE_URL}/events/{encoded}",
        f"{GAMMA_API_BASE_URL}/events/id/{encoded}",
    ):
        payload = await _fetch_json(url)
        if isinstance(payload, dict):
            return payload
    return None


async def _fetch_series(series_id: str) -> Optional[dict[str, Any]]:
    encoded = quote(str(series_id), safe="")
    payload = await _fetch_json(f"{GAMMA_API_BASE_URL}/series/{encoded}")
    return payload if isinstance(payload, dict) else None


async def get_market_configurations(
    series_id: Optional[str] = None,
    slug: Optional[str] = None,
    event_slug: Optional[str] = None,
    event_id: Optional[str] = None,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    if event_slug:
        event_payload = await _fetch_event_by_slug(event_slug)
        if not event_payload:
            return []
        recurrence_seconds = _parse_recurrence_seconds(event_payload.get("recurrence"))
        return _build_event_market_configurations(
            event_payload,
            include_closed=include_closed,
            series_id=str(event_payload.get("seriesId") or series_id or ""),
            recurrence_seconds=recurrence_seconds,
        )

    if event_id:
        event_payload = await _fetch_event_by_id(event_id)
        if not event_payload:
            return []
        recurrence_seconds = _parse_recurrence_seconds(event_payload.get("recurrence"))
        return _build_event_market_configurations(
            event_payload,
            include_closed=include_closed,
            series_id=str(event_payload.get("seriesId") or series_id or ""),
            recurrence_seconds=recurrence_seconds,
        )

    if slug:
        market_payload = await _fetch_market_by_slug(slug)
        if not market_payload:
            return []
        related_event = None
        event_slug_value = str(
            market_payload.get("eventSlug") or market_payload.get("event_slug") or ""
        ).strip()
        event_id_value = str(
            market_payload.get("eventId") or market_payload.get("event_id") or ""
        ).strip()
        if event_slug_value:
            related_event = await _fetch_event_by_slug(event_slug_value)
        elif event_id_value:
            related_event = await _fetch_event_by_id(event_id_value)
        recurrence_seconds = None
        if isinstance(related_event, dict):
            recurrence_seconds = _parse_recurrence_seconds(related_event.get("recurrence"))
        config_row = _build_market_configuration(
            market_payload,
            series_id=str(series_id or ""),
            event_payload=related_event,
            recurrence_seconds=recurrence_seconds,
        )
        return [config_row] if config_row else []

    if not series_id:
        return []

    series_payload = await _fetch_series(str(series_id))
    if not series_payload:
        return []

    recurrence_seconds = _parse_recurrence_seconds(series_payload.get("recurrence"))
    active_events = _extract_active_series_events(series_payload)
    if not active_events:
        events = series_payload.get("events", [])
        if isinstance(events, list):
            active_events = [event for event in events if isinstance(event, dict)]

    market_configs: list[dict[str, Any]] = []
    for event in active_events:
        embedded = _build_event_market_configurations(
            event,
            include_closed=include_closed,
            series_id=str(series_id),
            recurrence_seconds=recurrence_seconds,
        )
        if embedded:
            market_configs.extend(embedded)
            continue

        event_slug_value = str(event.get("slug") or "").strip()
        if event_slug_value:
            event_payload = await _fetch_event_by_slug(event_slug_value)
            if event_payload:
                expanded = _build_event_market_configurations(
                    event_payload,
                    include_closed=include_closed,
                    series_id=str(series_id),
                    recurrence_seconds=recurrence_seconds,
                )
                if expanded:
                    market_configs.extend(expanded)
                    continue
            market_payload = await _fetch_market_by_slug(event_slug_value)
            if market_payload:
                config_row = _build_market_configuration(
                    market_payload,
                    series_id=str(series_id),
                    event_payload=event_payload or event,
                    recurrence_seconds=recurrence_seconds,
                )
                if config_row and (
                    include_closed or str(config_row.get("status") or "").upper() == "OPEN"
                ):
                    market_configs.append(config_row)

    return market_configs


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ns_to_iso(epoch_ns: int) -> str:
    return datetime.fromtimestamp(epoch_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def format_elapsed(seconds: float) -> str:
    minutes, remainder = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{remainder:02d}"


def current_rss_bytes() -> Optional[int]:
    try:
        with open("/proc/self/statm", "r") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGESIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss if sys.platform == "darwin" else rss * 1024
    except OSError:
        return None


def format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * p
    lower = int(k)
    upper = min(lower + 1, len(sorted_values) - 1)
    interpolated = sorted_values[lower] + (
        (sorted_values[upper] - sorted_values[lower]) * (k - lower)
    )
    return round(interpolated, 3)


def summarize_distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(statistics.mean(values), 3),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def summarize_histogram(
    values: list[float],
    *,
    max_bins: int = 40,
) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "bin_count": 0,
            "min": None,
            "max": None,
            "bins": [],
        }

    ordered = sorted(values)
    min_value = ordered[0]
    max_value = ordered[-1]
    if len(ordered) == 1 or math.isclose(min_value, max_value):
        rounded = round(min_value, 3)
        return {
            "count": len(ordered),
            "bin_count": 1,
            "min": rounded,
            "max": rounded,
            "bins": [
                {
                    "start": rounded,
                    "end": rounded,
                    "count": len(ordered),
                }
            ],
        }

    bin_count = max(8, min(max_bins, int(math.sqrt(len(ordered)))))
    width = (max_value - min_value) / bin_count
    counts = [0 for _ in range(bin_count)]
    for value in ordered:
        index = min(bin_count - 1, int((value - min_value) / width))
        counts[index] += 1

    bins: list[dict[str, Any]] = []
    for index, count in enumerate(counts):
        start = min_value + index * width
        end = max_value if index == bin_count - 1 else min_value + (index + 1) * width
        bins.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "count": count,
            }
        )

    return {
        "count": len(ordered),
        "bin_count": bin_count,
        "min": round(min_value, 3),
        "max": round(max_value, 3),
        "bins": bins,
    }


def round_or_none(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def metric_from_distribution(distribution: dict[str, Any], key: str) -> Optional[float]:
    value = distribution.get(key)
    if value is None:
        return None
    return float(value)


def dedupe_preserve_order(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def parse_csv_event_types(raw_value: str) -> tuple[str, ...]:
    values = [part.strip().lower() for part in raw_value.split(",")]
    parsed = dedupe_preserve_order([value for value in values if value])
    if not parsed:
        raise ValueError("expected at least one event type")
    return parsed


def parse_csv_topologies(raw_value: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in raw_value.split(","):
        text = part.strip()
        if not text:
            continue
        size = int(text)
        if size <= 0:
            raise ValueError("topology sizes must be positive integers")
        values.append(size)
    parsed = tuple(dict.fromkeys(values))
    if not parsed:
        raise ValueError("expected at least one topology size")
    return parsed


def _default_cli_config() -> dict[str, Any]:
    return {
        "market": None,
        "series_id": None,
        "token_ids": [],
        "duration": 300.0,
        "topologies": "1,2,5,10",
        "warmup_seconds": 10.0,
        "warmup_compare_window_seconds": None,
        "ping_interval_seconds": 20.0,
        "event_retention_seconds": DEFAULT_EVENT_RETENTION_SECONDS,
        "output_dir": None,
        "event_types": "book,price_change,last_trade_price",
        "progress_interval_seconds": 5.0,
        "series_refresh_seconds": 5.0,
        "verbose": False,
        "write_visuals": False,
        "skip_visuals": False,
        "write_event_log": False,
        "skip_event_log": False,
        "write_connection_log": False,
        "skip_connection_log": False,
        "include_raw_event_payload": False,
    }


def _default_config_path() -> Path:
    return Path(__file__).resolve().with_name(DEFAULT_CONFIG_FILENAME)


def _normalize_config_key(key: str) -> str:
    aliases = {
        "market_slug": "market",
        "market_id": "market",
        "event_series_id": "series_id",
        "duration_seconds": "duration",
    }
    normalized = str(key).strip().replace("-", "_")
    return aliases.get(normalized, normalized)


def _coerce_config_values(raw_config: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for raw_key, value in raw_config.items():
        key = _normalize_config_key(raw_key)
        if key == "topologies":
            if isinstance(value, list):
                config[key] = ",".join(str(item) for item in value)
            else:
                config[key] = str(value)
            continue
        if key == "event_types":
            if isinstance(value, list):
                config[key] = ",".join(str(item) for item in value)
            else:
                config[key] = str(value)
            continue
        if key == "token_ids":
            if value is None:
                config[key] = []
            elif isinstance(value, list):
                config[key] = [str(item) for item in value]
            else:
                config[key] = [str(value)]
            continue
        if key == "output_dir":
            config[key] = None if value in {None, ""} else Path(str(value))
            continue
        config[key] = value
    return config


def load_config_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    lower_name = path.name.lower()
    if lower_name.endswith(".json") or lower_name.endswith(".json.example"):
        parsed = json.loads(raw.decode("utf-8"))
    elif (
        lower_name.endswith(".toml")
        or lower_name.endswith(".tml")
        or lower_name.endswith(".toml.example")
        or lower_name.endswith(".tml.example")
    ):
        if tomllib is None:
            raise ValueError("TOML config requires Python 3.11+")
        parsed = tomllib.loads(raw.decode("utf-8"))
    else:
        raise ValueError("config file must end in .json or .toml")
    if not isinstance(parsed, dict):
        raise ValueError("config file must contain a top-level object/table")
    return _coerce_config_values(parsed)


def parse_market_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


@dataclass(slots=True)
class ParsedVenueTimestamp:
    raw: Any
    epoch_ns: Optional[int]
    iso: Optional[str]
    parse_mode: str


def parse_venue_timestamp(value: Any) -> ParsedVenueTimestamp:
    if value is None:
        return ParsedVenueTimestamp(raw=value, epoch_ns=None, iso=None, parse_mode="missing")

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ParsedVenueTimestamp(
                raw=value, epoch_ns=None, iso=None, parse_mode="missing"
            )
        candidate: Any = text
    else:
        candidate = value

    if isinstance(candidate, datetime):
        dt = candidate if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)
        epoch_ns = int(dt.timestamp() * 1_000_000_000)
        return ParsedVenueTimestamp(
            raw=value,
            epoch_ns=epoch_ns,
            iso=dt.isoformat(),
            parse_mode="datetime",
        )

    if isinstance(candidate, str) and ("T" in candidate or candidate.endswith("Z")):
        normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            pass
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            epoch_ns = int(dt.timestamp() * 1_000_000_000)
            return ParsedVenueTimestamp(
                raw=value,
                epoch_ns=epoch_ns,
                iso=dt.isoformat(),
                parse_mode="iso8601",
            )

    numeric_int: Optional[int] = None
    numeric_float: Optional[float] = None
    if isinstance(candidate, int):
        numeric_int = candidate
    elif isinstance(candidate, str):
        if candidate.isdigit():
            numeric_int = int(candidate)
        else:
            try:
                numeric_float = float(candidate)
            except (TypeError, ValueError):
                return ParsedVenueTimestamp(
                    raw=value, epoch_ns=None, iso=None, parse_mode="invalid"
                )
    else:
        try:
            numeric_float = float(candidate)
        except (TypeError, ValueError):
            return ParsedVenueTimestamp(
                raw=value, epoch_ns=None, iso=None, parse_mode="invalid"
            )

    if numeric_int is not None:
        if numeric_int <= 0:
            return ParsedVenueTimestamp(
                raw=value, epoch_ns=None, iso=None, parse_mode="invalid"
            )
        magnitude = abs(numeric_int)
        if magnitude >= 1_000_000_000_000:
            epoch_ns = numeric_int * 1_000_000
            parse_mode = "milliseconds"
        elif magnitude >= 1_000_000_000:
            epoch_ns = numeric_int * 1_000_000_000
            parse_mode = "seconds"
        else:
            return ParsedVenueTimestamp(
                raw=value, epoch_ns=None, iso=None, parse_mode="invalid"
            )
    else:
        assert numeric_float is not None
        if not math.isfinite(numeric_float) or numeric_float <= 0:
            return ParsedVenueTimestamp(
                raw=value, epoch_ns=None, iso=None, parse_mode="invalid"
            )
        magnitude = abs(numeric_float)
        if magnitude >= 1_000_000_000_000:
            epoch_ns = int(round(numeric_float * 1_000_000))
            parse_mode = "milliseconds"
        elif magnitude >= 1_000_000_000:
            epoch_ns = int(round(numeric_float * 1_000_000_000))
            parse_mode = "seconds"
        else:
            return ParsedVenueTimestamp(
                raw=value, epoch_ns=None, iso=None, parse_mode="invalid"
            )

    try:
        dt = datetime.fromtimestamp(epoch_ns / 1_000_000_000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ParsedVenueTimestamp(raw=value, epoch_ns=None, iso=None, parse_mode="invalid")

    return ParsedVenueTimestamp(
        raw=value,
        epoch_ns=epoch_ns,
        iso=dt.isoformat(),
        parse_mode=parse_mode,
    )


def normalize_event_type(raw_value: Any) -> str:
    return str(raw_value or "").strip().lower()


def extract_asset_ids(raw_event: dict[str, Any]) -> tuple[str, ...]:
    asset_ids: list[str] = []
    top_level = raw_event.get("asset_id") or raw_event.get("assetId")
    if top_level:
        asset_ids.append(str(top_level))
    for change in raw_event.get("price_changes", []) or []:
        if isinstance(change, dict):
            asset_id = change.get("asset_id") or change.get("assetId")
            if asset_id:
                asset_ids.append(str(asset_id))
    return dedupe_preserve_order(asset_ids)


def sanitize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_for_hash(item)
            for key, item in sorted(value.items())
            if key not in LOCAL_DIAGNOSTIC_FIELDS
        }
    if isinstance(value, list):
        sanitized_items = [sanitize_for_hash(item) for item in value]
        return sorted(
            sanitized_items,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        )
    return value


@dataclass(slots=True)
class BenchmarkTarget:
    market_slug: Optional[str]
    token_ids: tuple[str, ...]
    series_id: Optional[str]
    segment_id: str
    switch_reason: str
    start_at: Optional[datetime]
    end_at: Optional[datetime]

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_slug": self.market_slug,
            "token_ids": list(self.token_ids),
            "series_id": self.series_id,
            "segment_id": self.segment_id,
            "switch_reason": self.switch_reason,
            "start_at": iso_or_none(self.start_at),
            "end_at": iso_or_none(self.end_at),
        }


@dataclass(slots=True)
class MarketSegmentRecord:
    segment_id: str
    market_slug: Optional[str]
    series_id: Optional[str]
    token_ids: tuple[str, ...]
    switch_reason: str
    started_at_ns: int
    started_at_iso: str
    ended_at_ns: Optional[int] = None
    ended_at_iso: Optional[str] = None

    def close(self, ended_at_ns: int) -> None:
        self.ended_at_ns = ended_at_ns
        self.ended_at_iso = ns_to_iso(ended_at_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "market_slug": self.market_slug,
            "series_id": self.series_id,
            "token_ids": list(self.token_ids),
            "switch_reason": self.switch_reason,
            "started_at_ns": self.started_at_ns,
            "started_at_iso": self.started_at_iso,
            "ended_at_ns": self.ended_at_ns,
            "ended_at_iso": self.ended_at_iso,
        }


def build_event_key(
    event_type: str,
    raw_event: dict[str, Any],
    *,
    scope: Optional[str] = None,
) -> str:
    normalized_type = normalize_event_type(event_type)
    prefix = f"{scope}:" if scope else ""
    if normalized_type == "last_trade_price":
        trade_id = str(raw_event.get("id") or "").strip()
        if trade_id:
            return f"{prefix}{normalized_type}:{trade_id}"
    # Polymarket book events carry a venue-computed `hash` of the current book
    # state. When present it's already unique per emission, so use it directly
    # instead of re-serializing + hashing the full payload.
    if normalized_type == "book":
        book_hash = raw_event.get("hash")
        if isinstance(book_hash, str) and book_hash:
            return f"{prefix}{normalized_type}:{book_hash}"

    payload = _fast_canonical_dumps(sanitize_for_hash(raw_event))
    digest = hashlib.sha256(payload).hexdigest()
    return f"{prefix}{normalized_type}:{digest}"


@dataclass(slots=True)
class Observation:
    event_key: str
    event_type: str
    asset_id: Optional[str]
    asset_ids: tuple[str, ...]
    market_slug: Optional[str]
    series_id: Optional[str]
    segment_id: str
    switch_reason: str
    phase_kind: str
    connection_id: str
    topology_id: str
    topology_size: int
    received_at_ns: int
    received_at_iso: str
    in_warmup: bool
    venue_timestamp_raw: Any
    venue_timestamp_ns: Optional[int]
    venue_timestamp_iso: Optional[str]
    venue_timestamp_parse_mode: str
    raw_event: Optional[dict[str, Any]] = None

    def to_record(self, *, include_raw_event: bool = False) -> dict[str, Any]:
        record = {
            "event_key": self.event_key,
            "event_type": self.event_type,
            "asset_id": self.asset_id,
            "asset_ids": list(self.asset_ids),
            "market_slug": self.market_slug,
            "series_id": self.series_id,
            "segment_id": self.segment_id,
            "switch_reason": self.switch_reason,
            "phase_kind": self.phase_kind,
            "connection_id": self.connection_id,
            "topology_id": self.topology_id,
            "topology_size": self.topology_size,
            "received_at_ns": self.received_at_ns,
            "received_at_iso": self.received_at_iso,
            "in_warmup": self.in_warmup,
            "venue_timestamp_raw": self.venue_timestamp_raw,
            "venue_timestamp_ns": self.venue_timestamp_ns,
            "venue_timestamp_iso": self.venue_timestamp_iso,
            "venue_timestamp_parse_mode": self.venue_timestamp_parse_mode,
        }
        if include_raw_event:
            record["raw_event"] = self.raw_event
        return record


@dataclass(slots=True)
class PendingEventAggregate:
    venue_timestamp_ns: Optional[int]
    first_received_at_ns: int
    first_topology_index: int
    first_connection_index: int
    received_by_topology: array
    received_by_connection: array
    event_type: str = ""


@dataclass(slots=True)
class TimestampParseabilityStats:
    parsed: int = 0
    missing: int = 0
    invalid: int = 0
    by_mode: Counter[str] = field(default_factory=Counter)

    def record(self, parse_mode: str) -> None:
        self.by_mode[parse_mode] += 1
        if parse_mode == "missing":
            self.missing += 1
        elif parse_mode == "invalid":
            self.invalid += 1
        else:
            self.parsed += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed": self.parsed,
            "missing": self.missing,
            "invalid": self.invalid,
            "by_mode": dict(sorted(self.by_mode.items())),
        }


@dataclass(slots=True)
class ConnectionRuntimeStats:
    connection_id: str
    topology_id: str
    topology_size: int
    connection_attempts: int = 0
    successful_connects: int = 0
    reconnects: int = 0
    disconnects: int = 0
    connect_failures: int = 0
    total_messages: int = 0
    total_events: int = 0
    malformed_messages: int = 0
    control_messages: int = 0
    filtered_messages: int = 0
    ignored_items: int = 0
    connected: bool = False
    last_error: Optional[str] = None
    connected_at_monotonic: Optional[float] = None
    last_message_monotonic: Optional[float] = None
    connected_at_ns: Optional[int] = None
    phase_started_ns: Optional[int] = None
    warmup_end_ns: Optional[int] = None
    post_warmup_compare_end_ns: Optional[int] = None
    market_rebinds: int = 0
    warmup_resets: int = 0
    current_market_slug: Optional[str] = None
    current_series_id: Optional[str] = None
    current_segment_id: Optional[str] = None
    switch_reason: Optional[str] = None
    longest_silence_seconds: float = 0.0

    def note_connect(
        self,
        now_monotonic: float,
        now_ns: int,
        warmup_seconds: float,
    ) -> None:
        self.connection_attempts += 1
        self.successful_connects += 1
        if self.successful_connects > 1:
            self.reconnects += 1
        self.connected = True
        self.connected_at_monotonic = now_monotonic
        self.connected_at_ns = now_ns
        self.last_message_monotonic = now_monotonic
        self.last_error = None

    def note_connect_failure(self, exc: BaseException) -> None:
        self.connection_attempts += 1
        self.connect_failures += 1
        self.last_error = str(exc)

    def note_disconnect(self, exc: BaseException) -> None:
        if self.connected:
            self.disconnects += 1
        self.connected = False
        self.last_error = str(exc)

    def note_message(self, now_monotonic: float) -> None:
        if self.last_message_monotonic is not None:
            silence = now_monotonic - self.last_message_monotonic
            self.longest_silence_seconds = max(self.longest_silence_seconds, silence)
        self.last_message_monotonic = now_monotonic

    def current_silence_seconds(self, now_monotonic: float) -> Optional[float]:
        if self.last_message_monotonic is None:
            return None
        return max(0.0, now_monotonic - self.last_message_monotonic)

    def reset_phase_window(
        self,
        now_ns: int,
        warmup_seconds: float,
        compare_window_seconds: float,
    ) -> None:
        self.phase_started_ns = now_ns
        self.warmup_end_ns = now_ns + int(max(0.0, warmup_seconds) * 1_000_000_000)
        self.post_warmup_compare_end_ns = self.warmup_end_ns + int(
            max(0.0, compare_window_seconds) * 1_000_000_000
        )
        self.warmup_resets += 1

    def note_market_target(
        self,
        *,
        now_ns: int,
        market_slug: Optional[str],
        series_id: Optional[str],
        segment_id: str,
        switch_reason: str,
        warmup_seconds: float,
        compare_window_seconds: float,
    ) -> None:
        if self.current_segment_id is not None and self.current_segment_id != segment_id:
            self.market_rebinds += 1
        self.current_market_slug = market_slug
        self.current_series_id = series_id
        self.current_segment_id = segment_id
        self.switch_reason = switch_reason
        self.reset_phase_window(now_ns, warmup_seconds, compare_window_seconds)

    def in_warmup(self, received_at_ns: int) -> bool:
        if self.warmup_end_ns is None:
            return False
        return received_at_ns < self.warmup_end_ns

    def phase_kind(self, received_at_ns: int) -> str:
        if self.in_warmup(received_at_ns):
            return "warmup"
        if (
            self.post_warmup_compare_end_ns is not None
            and received_at_ns < self.post_warmup_compare_end_ns
        ):
            return "post_warmup_compare"
        return "steady"

    def warmup_remaining_seconds(self, now_ns: int) -> Optional[float]:
        if self.warmup_end_ns is None:
            return None
        return max(0.0, (self.warmup_end_ns - now_ns) / 1_000_000_000)

    def snapshot(self, now_monotonic: float) -> dict[str, Any]:
        current_silence = self.current_silence_seconds(now_monotonic)
        now_ns = time.time_ns()
        warmup_remaining = self.warmup_remaining_seconds(now_ns)
        return {
            "connection_id": self.connection_id,
            "topology_id": self.topology_id,
            "topology_size": self.topology_size,
            "connected": self.connected,
            "connection_attempts": self.connection_attempts,
            "successful_connects": self.successful_connects,
            "reconnects": self.reconnects,
            "disconnects": self.disconnects,
            "connect_failures": self.connect_failures,
            "market_rebinds": self.market_rebinds,
            "warmup_resets": self.warmup_resets,
            "current_market_slug": self.current_market_slug,
            "current_series_id": self.current_series_id,
            "current_segment_id": self.current_segment_id,
            "switch_reason": self.switch_reason,
            "total_messages": self.total_messages,
            "total_events": self.total_events,
            "malformed_messages": self.malformed_messages,
            "control_messages": self.control_messages,
            "filtered_messages": self.filtered_messages,
            "ignored_items": self.ignored_items,
            "current_silence_seconds": (
                round(current_silence, 3) if current_silence is not None else None
            ),
            "longest_silence_seconds": round(self.longest_silence_seconds, 3),
            "in_warmup": self.in_warmup(now_ns),
            "warmup_remaining_seconds": round_or_none(warmup_remaining),
            "last_error": self.last_error,
        }


@dataclass(slots=True)
class BenchmarkConfig:
    market_slug: Optional[str]
    token_ids: tuple[str, ...]
    series_id: Optional[str] = None
    duration_seconds: float = 300.0
    topologies: tuple[int, ...] = (1, 2, 5, 10)
    warmup_seconds: float = 10.0
    warmup_compare_window_seconds: Optional[float] = None
    ping_interval_seconds: float = 20.0
    progress_interval_seconds: float = 5.0
    series_refresh_seconds: float = 5.0
    event_types: tuple[str, ...] = DEFAULT_EVENT_TYPES
    output_dir: Path = Path("recordings/ws-bench")
    ws_url: str = DEFAULT_WS_URL
    reconnect_delay_seconds: float = 1.0
    open_timeout_seconds: float = 10.0
    event_retention_seconds: float = DEFAULT_EVENT_RETENTION_SECONDS
    generate_visuals: bool = False
    write_event_log: bool = False
    write_connection_log: bool = False
    include_raw_event_payload: bool = False
    verbose: bool = False


@dataclass(slots=True)
class StreamingDistribution:
    reservoir_size: int = DEFAULT_DISTRIBUTION_SAMPLE_SIZE
    count: int = 0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    sum_value: float = 0.0
    sample: list[float] = field(default_factory=list)
    _rng: random.Random = field(default_factory=lambda: random.Random(0))

    def add(self, value: float) -> None:
        numeric = float(value)
        if not math.isfinite(numeric):
            return
        self.count += 1
        self.sum_value += numeric
        self.min_value = numeric if self.min_value is None else min(self.min_value, numeric)
        self.max_value = numeric if self.max_value is None else max(self.max_value, numeric)
        if len(self.sample) < self.reservoir_size:
            self.sample.append(numeric)
            return
        sample_index = self._rng.randrange(self.count)
        if sample_index < self.reservoir_size:
            self.sample[sample_index] = numeric

    def to_distribution(self) -> dict[str, Any]:
        if self.count == 0:
            return {
                "count": 0,
                "sample_count": 0,
                "approximate": False,
                "min": None,
                "max": None,
                "mean": None,
                "p50": None,
                "p95": None,
                "p99": None,
            }
        sorted_sample = sorted(self.sample)
        approximate = self.count > len(sorted_sample)
        return {
            "count": self.count,
            "sample_count": len(sorted_sample),
            "approximate": approximate,
            "min": round(self.min_value, 3) if self.min_value is not None else None,
            "max": round(self.max_value, 3) if self.max_value is not None else None,
            "mean": round(self.sum_value / self.count, 3) if self.count else None,
            "p50": percentile(sorted_sample, 0.50),
            "p95": percentile(sorted_sample, 0.95),
            "p99": percentile(sorted_sample, 0.99),
        }

    def to_histogram(self, *, max_bins: int = 40) -> dict[str, Any]:
        if self.count == 0:
            return {
                "count": 0,
                "sample_count": 0,
                "approximate": False,
                "bin_count": 0,
                "min": None,
                "max": None,
                "bins": [],
            }

        sample_hist = summarize_histogram(self.sample, max_bins=max_bins)
        approximate = self.count > len(self.sample)
        bins = [dict(bin_row) for bin_row in sample_hist["bins"]]
        if approximate and bins:
            scaled_counts = _scale_histogram_counts(
                [int(bin_row["count"]) for bin_row in bins],
                target_total=self.count,
            )
            for bin_row, scaled_count in zip(bins, scaled_counts):
                bin_row["count"] = scaled_count

        return {
            "count": self.count,
            "sample_count": len(self.sample),
            "approximate": approximate,
            "bin_count": sample_hist["bin_count"],
            "min": round(self.min_value, 3) if self.min_value is not None else None,
            "max": round(self.max_value, 3) if self.max_value is not None else None,
            "bins": bins,
        }


def _scale_histogram_counts(sample_counts: list[int], *, target_total: int) -> list[int]:
    if not sample_counts:
        return []
    sample_total = sum(sample_counts)
    if sample_total <= 0 or sample_total == target_total:
        return list(sample_counts)

    scale = target_total / sample_total
    scaled = [count * scale for count in sample_counts]
    floored = [int(value) for value in scaled]
    remainder = target_total - sum(floored)
    if remainder > 0:
        order = sorted(
            range(len(sample_counts)),
            key=lambda idx: (scaled[idx] - floored[idx], sample_counts[idx]),
            reverse=True,
        )
        for idx in order[:remainder]:
            floored[idx] += 1
    return floored


@dataclass(slots=True)
class GapRunTracker:
    event_count_distribution: StreamingDistribution = field(default_factory=StreamingDistribution)
    duration_distribution_ms: StreamingDistribution = field(default_factory=StreamingDistribution)
    total_missed_events: int = 0
    run_count: int = 0
    largest_gap_record: Optional[dict[str, Any]] = None
    current_start_ns: Optional[int] = None
    current_last_ns: Optional[int] = None
    current_start_key: Optional[str] = None
    current_end_key: Optional[str] = None
    current_count: int = 0

    def note_miss(self, *, event_key: str, received_at_ns: int) -> None:
        self.total_missed_events += 1
        if self.current_start_ns is None:
            self.current_start_ns = received_at_ns
            self.current_start_key = event_key
        self.current_last_ns = received_at_ns
        self.current_end_key = event_key
        self.current_count += 1

    def note_seen(self) -> None:
        self._close_run()

    def finalize(self) -> None:
        self._close_run()

    def _close_run(self) -> None:
        if self.current_start_ns is None or self.current_last_ns is None or self.current_count == 0:
            return

        duration_ms = (self.current_last_ns - self.current_start_ns) / 1_000_000
        self.run_count += 1
        self.event_count_distribution.add(float(self.current_count))
        self.duration_distribution_ms.add(duration_ms)

        candidate = {
            "events": self.current_count,
            "duration_ms": round(duration_ms, 3),
            "started_at": ns_to_iso(self.current_start_ns),
            "ended_at": ns_to_iso(self.current_last_ns),
            "start_event_key": self.current_start_key,
            "end_event_key": self.current_end_key,
        }
        if (
            self.largest_gap_record is None
            or candidate["events"] > self.largest_gap_record["events"]
            or (
                candidate["events"] == self.largest_gap_record["events"]
                and candidate["duration_ms"] > self.largest_gap_record["duration_ms"]
            )
        ):
            self.largest_gap_record = candidate

        self.current_start_ns = None
        self.current_last_ns = None
        self.current_start_key = None
        self.current_end_key = None
        self.current_count = 0

    def to_summary(self) -> dict[str, Any]:
        largest_gap_events = self.largest_gap_record["events"] if self.largest_gap_record else 0
        largest_gap_duration_ms = (
            self.largest_gap_record["duration_ms"] if self.largest_gap_record else None
        )
        return {
            "relative_gap_runs": self.run_count,
            "relative_gap_events_total": self.total_missed_events,
            "relative_gap_events": self.event_count_distribution.to_distribution(),
            "relative_gap_duration_ms": self.duration_distribution_ms.to_distribution(),
            "largest_relative_gap_events": largest_gap_events,
            "largest_relative_gap_ms": largest_gap_duration_ms,
            "largest_relative_gap": self.largest_gap_record,
        }


@dataclass(slots=True)
class ReceiverRollup:
    seen_events: int = 0
    first_seen_wins: int = 0
    arrival_delta_ms: StreamingDistribution = field(default_factory=StreamingDistribution)
    freshness_ms: StreamingDistribution = field(default_factory=StreamingDistribution)
    book_age_ms: StreamingDistribution = field(default_factory=StreamingDistribution)
    inter_event_gap_ms: StreamingDistribution = field(default_factory=StreamingDistribution)
    last_received_at_ns: Optional[int] = None
    longest_inter_event_gap_ms: Optional[float] = None
    gaps: GapRunTracker = field(default_factory=GapRunTracker)

    def note_seen(
        self,
        *,
        received_at_ns: int,
        event_first_received_at_ns: int,
        venue_timestamp_ns: Optional[int],
        is_first_seen: bool,
        event_type: str,
    ) -> None:
        self.gaps.note_seen()
        self.seen_events += 1
        if is_first_seen:
            self.first_seen_wins += 1
        self.arrival_delta_ms.add(
            (received_at_ns - event_first_received_at_ns) / 1_000_000
        )
        if venue_timestamp_ns is not None:
            age_ms = (received_at_ns - venue_timestamp_ns) / 1_000_000
            # `book` events carry a "last order-book change" timestamp, not an emit
            # timestamp, so age-since-timestamp is not a delivery-latency signal for
            # them. Keep it separately for diagnostics; exclude from freshness_ms.
            if event_type == "book":
                self.book_age_ms.add(age_ms)
            else:
                self.freshness_ms.add(age_ms)
        if self.last_received_at_ns is not None:
            gap_ms = (received_at_ns - self.last_received_at_ns) / 1_000_000
            self.inter_event_gap_ms.add(gap_ms)
            self.longest_inter_event_gap_ms = (
                gap_ms
                if self.longest_inter_event_gap_ms is None
                else max(self.longest_inter_event_gap_ms, gap_ms)
            )
        self.last_received_at_ns = received_at_ns

    def note_miss(self, *, event_key: str, received_at_ns: int) -> None:
        self.gaps.note_miss(event_key=event_key, received_at_ns=received_at_ns)

    def finalize(self) -> None:
        self.gaps.finalize()

    def summary_fields(self) -> dict[str, Any]:
        return {
            "seen_event_count": self.seen_events,
            "first_seen_wins": self.first_seen_wins,
            "arrival_delta_ms": self.arrival_delta_ms.to_distribution(),
            "arrival_delta_histogram_ms": self.arrival_delta_ms.to_histogram(),
            "freshness_ms": self.freshness_ms.to_distribution(),
            "freshness_histogram_ms": self.freshness_ms.to_histogram(),
            "book_age_ms": self.book_age_ms.to_distribution(),
            "book_age_histogram_ms": self.book_age_ms.to_histogram(),
            "inter_event_gap_ms": self.inter_event_gap_ms.to_distribution(),
            "longest_inter_event_gap_ms": round_or_none(self.longest_inter_event_gap_ms, 3),
            **self.gaps.to_summary(),
        }


class MetricsAggregator:
    def __init__(
        self,
        *,
        topology_ids: Sequence[str],
        connection_ids_by_topology: dict[str, list[str]],
        scoring_filter: Optional[Callable[[Observation], bool]] = None,
        event_retention_seconds: float = DEFAULT_EVENT_RETENTION_SECONDS,
    ) -> None:
        self._topology_ids = tuple(topology_ids)
        self._connection_ids_by_topology = {
            topology_id: list(connection_ids)
            for topology_id, connection_ids in connection_ids_by_topology.items()
        }
        self._connection_ids = tuple(
            connection_id
            for topology_id in self._topology_ids
            for connection_id in self._connection_ids_by_topology.get(topology_id, [])
        )
        self._topology_index = {
            topology_id: idx for idx, topology_id in enumerate(self._topology_ids)
        }
        self._connection_index = {
            connection_id: idx for idx, connection_id in enumerate(self._connection_ids)
        }
        self._scoring_filter = scoring_filter or (lambda observation: not observation.in_warmup)
        self._event_retention_ns = int(max(0.0, event_retention_seconds) * 1_000_000_000)
        self._pending_events: dict[str, PendingEventAggregate] = {}
        self._pending_order: deque[tuple[int, str]] = deque()
        self._topology_rollups = [ReceiverRollup() for _ in self._topology_ids]
        self._connection_rollups = [ReceiverRollup() for _ in self._connection_ids]
        self.topology_observation_counts: Counter[str] = Counter()
        self.topology_duplicate_counts: Counter[str] = Counter()
        self.connection_observation_counts: Counter[str] = Counter()
        self.connection_duplicate_counts: Counter[str] = Counter()
        self.parseability_all = TimestampParseabilityStats()
        self.parseability_scored = TimestampParseabilityStats()
        self.parseability_by_event_type_all: dict[str, TimestampParseabilityStats] = {}
        self.parseability_by_event_type_scored: dict[str, TimestampParseabilityStats] = {}
        self.all_observations = 0
        self.scored_observations = 0
        self.finalized_union_event_count = 0
        self.first_union_event_ns: Optional[int] = None
        self.last_union_event_ns: Optional[int] = None
        self.union_inter_event_gap_ms = StreamingDistribution()

    def record_observation(self, observation: Observation) -> None:
        self.all_observations += 1
        self.parseability_all.record(observation.venue_timestamp_parse_mode)
        self._stats_for_event_type(
            self.parseability_by_event_type_all, observation.event_type
        ).record(observation.venue_timestamp_parse_mode)

        if not self._scoring_filter(observation):
            return

        self.scored_observations += 1
        self.parseability_scored.record(observation.venue_timestamp_parse_mode)
        self._stats_for_event_type(
            self.parseability_by_event_type_scored, observation.event_type
        ).record(observation.venue_timestamp_parse_mode)

        self.topology_observation_counts[observation.topology_id] += 1
        self.connection_observation_counts[observation.connection_id] += 1

        topology_idx = self._topology_index[observation.topology_id]
        connection_idx = self._connection_index[observation.connection_id]

        aggregate = self._pending_events.get(observation.event_key)
        if aggregate is None:
            aggregate = PendingEventAggregate(
                venue_timestamp_ns=observation.venue_timestamp_ns,
                first_received_at_ns=observation.received_at_ns,
                first_topology_index=topology_idx,
                first_connection_index=connection_idx,
                received_by_topology=array("q", [-1] * len(self._topology_ids)),
                received_by_connection=array("q", [-1] * len(self._connection_ids)),
                event_type=observation.event_type,
            )
            self._pending_events[observation.event_key] = aggregate
            self._pending_order.append((aggregate.first_received_at_ns, observation.event_key))
        else:
            if aggregate.venue_timestamp_ns is None and observation.venue_timestamp_ns is not None:
                aggregate.venue_timestamp_ns = observation.venue_timestamp_ns
            if observation.received_at_ns < aggregate.first_received_at_ns:
                aggregate.first_received_at_ns = observation.received_at_ns
                aggregate.first_topology_index = topology_idx
                aggregate.first_connection_index = connection_idx

        if aggregate.received_by_topology[topology_idx] >= 0:
            self.topology_duplicate_counts[observation.topology_id] += 1
        else:
            aggregate.received_by_topology[topology_idx] = observation.received_at_ns

        if aggregate.received_by_connection[connection_idx] >= 0:
            self.connection_duplicate_counts[observation.connection_id] += 1
        else:
            aggregate.received_by_connection[connection_idx] = observation.received_at_ns

        self._finalize_ready(cutoff_ns=observation.received_at_ns - self._event_retention_ns)

    def snapshot(self) -> dict[str, Any]:
        union_size = self.finalized_union_event_count
        seen_counts = [state.seen_events for state in self._topology_rollups]
        first_wins = [state.first_seen_wins for state in self._topology_rollups]

        for aggregate in self._pending_events.values():
            union_size += 1
            first_wins[aggregate.first_topology_index] += 1
            for idx, received_at in enumerate(aggregate.received_by_topology):
                if received_at >= 0:
                    seen_counts[idx] += 1

        topology_rows: dict[str, dict[str, Any]] = {}
        for idx, topology_id in enumerate(self._topology_ids):
            seen_events = seen_counts[idx]
            topology_first_wins = first_wins[idx]
            miss_count = max(0, union_size - seen_events)
            topology_rows[topology_id] = {
                "seen_events": seen_events,
                "relative_miss_count": miss_count,
                "first_seen_wins": topology_first_wins,
                "coverage_rate": round(seen_events / union_size, 6) if union_size else 0.0,
                "first_seen_win_rate": round(topology_first_wins / union_size, 6)
                if union_size
                else 0.0,
            }
        return {
            "union_event_count": union_size,
            "topologies": topology_rows,
        }

    def build_summary(
        self,
        *,
        config: BenchmarkConfig,
        token_ids: Sequence[str],
        run_started_at: str,
        run_started_ns: int,
        run_ended_at: str,
        run_ended_ns: int,
        output_dir: Path,
        connection_stats: dict[str, ConnectionRuntimeStats],
    ) -> dict[str, Any]:
        self._flush_pending(force=True)
        for rollup in self._topology_rollups:
            rollup.finalize()
        for rollup in self._connection_rollups:
            rollup.finalize()

        union_size = self.finalized_union_event_count

        topology_summaries: dict[str, Any] = {}
        for topology_idx, topology_id in enumerate(self._topology_ids):
            topology_size = int(topology_id)
            rollup = self._topology_rollups[topology_idx]
            miss_count = max(0, union_size - rollup.seen_events)
            observation_count = self.topology_observation_counts.get(topology_id, 0)
            duplicate_count = self.topology_duplicate_counts.get(topology_id, 0)
            topology_summaries[topology_id] = {
                "topology_size": topology_size,
                "connection_ids": self._connection_ids_by_topology.get(topology_id, []),
                "event_observations": observation_count,
                "duplicate_observations": duplicate_count,
                "intra_topology_dup_rate": round(
                    duplicate_count / observation_count, 6
                )
                if observation_count
                else 0.0,
                "seen_event_count": rollup.seen_events,
                "relative_loss_count": miss_count,
                "coverage_rate": round(rollup.seen_events / union_size, 6)
                if union_size
                else 0.0,
                "relative_miss_rate": round(miss_count / union_size, 6)
                if union_size
                else 0.0,
                "first_seen_wins": rollup.first_seen_wins,
                "first_seen_win_rate": round(rollup.first_seen_wins / union_size, 6)
                if union_size
                else 0.0,
                **rollup.summary_fields(),
            }

        connection_summaries: dict[str, Any] = {}
        for connection_idx, connection_id in enumerate(self._connection_ids):
            runtime = connection_stats[connection_id]
            rollup = self._connection_rollups[connection_idx]
            observation_count = self.connection_observation_counts.get(connection_id, 0)
            duplicate_count = self.connection_duplicate_counts.get(connection_id, 0)
            miss_count = max(0, union_size - rollup.seen_events)
            connection_summaries[connection_id] = {
                **runtime.snapshot(time.monotonic()),
                "scored_event_observations": observation_count,
                "seen_event_count": rollup.seen_events,
                "relative_loss_count": miss_count,
                "coverage_rate": round(rollup.seen_events / union_size, 6)
                if union_size
                else 0.0,
                "relative_miss_rate": round(miss_count / union_size, 6) if union_size else 0.0,
                "first_seen_wins": rollup.first_seen_wins,
                "first_seen_win_rate": round(rollup.first_seen_wins / union_size, 6)
                if union_size
                else 0.0,
                "duplicate_observations": duplicate_count,
                "intra_connection_dup_rate": round(
                    duplicate_count / observation_count, 6
                )
                if observation_count
                else 0.0,
                **rollup.summary_fields(),
            }

        summary = {
            "run_metadata": {
                "market_slug": config.market_slug,
                "token_ids": list(token_ids),
                "duration_seconds": config.duration_seconds,
                "warmup_seconds": config.warmup_seconds,
                "progress_interval_seconds": config.progress_interval_seconds,
                "topologies": list(config.topologies),
                "event_types": list(config.event_types),
                "endpoint": config.ws_url,
                "event_retention_seconds": config.event_retention_seconds,
                "distribution_sample_size": DEFAULT_DISTRIBUTION_SAMPLE_SIZE,
                "started_at": run_started_at,
                "ended_at": run_ended_at,
                "output_dir": str(output_dir),
                "distribution_histogram_max_bins": 40,
                "visuals_enabled": config.generate_visuals,
                "events_log_enabled": config.write_event_log,
                "connection_log_enabled": config.write_connection_log,
                "events_log_includes_raw_payload": config.include_raw_event_payload,
            },
            "scored_union_event_count": union_size,
            "all_observations": self.all_observations,
            "scored_observations": self.scored_observations,
            "activity_window": self._build_activity_window(
                run_started_ns=run_started_ns,
                run_ended_ns=run_ended_ns,
            ),
            "topologies": topology_summaries,
            "connections": connection_summaries,
            "timestamp_parseability": {
                "all_observations": self.parseability_all.to_dict(),
                "scored_observations": self.parseability_scored.to_dict(),
                "by_event_type_all": {
                    event_type: stats.to_dict()
                    for event_type, stats in sorted(
                        self.parseability_by_event_type_all.items()
                    )
                },
                "by_event_type_scored": {
                    event_type: stats.to_dict()
                    for event_type, stats in sorted(
                        self.parseability_by_event_type_scored.items()
                    )
                },
            },
            "caveats": [
                "Relative loss is topology-relative within this run, not authoritative venue loss.",
                "Freshness is measured over price_change and last_trade_price events only; book events carry a last-changed timestamp (not an emit time) and are reported separately as book_age_ms.",
                "Warmup is applied per connection and after reconnects; those observations are recorded but excluded from scored metrics.",
                f"Unique event aggregation uses a rolling {config.event_retention_seconds:.1f}s retention window to keep memory bounded on long runs.",
                f"Latency/freshness percentiles and histograms are estimated from bounded reservoir samples of up to {DEFAULT_DISTRIBUTION_SAMPLE_SIZE} values per metric.",
            ],
        }
        summary["comparative_insights"] = build_comparative_insights(topology_summaries)
        return summary

    def _build_activity_window(
        self,
        *,
        run_started_ns: int,
        run_ended_ns: int,
    ) -> dict[str, Any]:
        if self.finalized_union_event_count == 0 or self.first_union_event_ns is None:
            return {
                "first_scored_event_at": None,
                "last_scored_event_at": None,
                "first_scored_event_offset_seconds": None,
                "scored_event_span_seconds": 0.0,
                "run_tail_silence_seconds": round(
                    (run_ended_ns - run_started_ns) / 1_000_000_000,
                    3,
                ),
                "union_inter_event_gap_ms": self.union_inter_event_gap_ms.to_distribution(),
                "longest_union_inter_event_gap_ms": None,
            }

        last_scored_ns = self.last_union_event_ns or self.first_union_event_ns
        return {
            "first_scored_event_at": ns_to_iso(self.first_union_event_ns),
            "last_scored_event_at": ns_to_iso(last_scored_ns),
            "first_scored_event_offset_seconds": round(
                (self.first_union_event_ns - run_started_ns) / 1_000_000_000,
                3,
            ),
            "scored_event_span_seconds": round(
                (last_scored_ns - self.first_union_event_ns) / 1_000_000_000,
                3,
            ),
            "run_tail_silence_seconds": round(
                max(0, run_ended_ns - last_scored_ns) / 1_000_000_000,
                3,
            ),
            "union_inter_event_gap_ms": self.union_inter_event_gap_ms.to_distribution(),
            "longest_union_inter_event_gap_ms": round_or_none(
                self.union_inter_event_gap_ms.max_value,
                3,
            ),
        }

    @staticmethod
    def _stats_for_event_type(
        mapping: dict[str, TimestampParseabilityStats], event_type: str
    ) -> TimestampParseabilityStats:
        stats = mapping.get(event_type)
        if stats is None:
            stats = TimestampParseabilityStats()
            mapping[event_type] = stats
        return stats

    def _finalize_ready(self, *, cutoff_ns: int) -> None:
        while self._pending_order and self._pending_order[0][0] <= cutoff_ns:
            _, event_key = self._pending_order.popleft()
            aggregate = self._pending_events.pop(event_key, None)
            if aggregate is None:
                continue
            self._finalize_event(event_key=event_key, aggregate=aggregate)

    def _flush_pending(self, *, force: bool = False) -> None:
        if force:
            while self._pending_order:
                _, event_key = self._pending_order.popleft()
                aggregate = self._pending_events.pop(event_key, None)
                if aggregate is None:
                    continue
                self._finalize_event(event_key=event_key, aggregate=aggregate)
            return
        if self._pending_order:
            latest_ns = self._pending_order[-1][0]
            self._finalize_ready(cutoff_ns=latest_ns - self._event_retention_ns)

    def _finalize_event(
        self,
        *,
        event_key: str,
        aggregate: PendingEventAggregate,
    ) -> None:
        self.finalized_union_event_count += 1
        if self.first_union_event_ns is None:
            self.first_union_event_ns = aggregate.first_received_at_ns
        if self.last_union_event_ns is not None:
            self.union_inter_event_gap_ms.add(
                (aggregate.first_received_at_ns - self.last_union_event_ns) / 1_000_000
            )
        self.last_union_event_ns = aggregate.first_received_at_ns

        for topology_idx, rollup in enumerate(self._topology_rollups):
            received_at = int(aggregate.received_by_topology[topology_idx])
            if received_at >= 0:
                rollup.note_seen(
                    received_at_ns=received_at,
                    event_first_received_at_ns=aggregate.first_received_at_ns,
                    venue_timestamp_ns=aggregate.venue_timestamp_ns,
                    is_first_seen=aggregate.first_topology_index == topology_idx,
                    event_type=aggregate.event_type,
                )
            else:
                rollup.note_miss(
                    event_key=event_key,
                    received_at_ns=aggregate.first_received_at_ns,
                )

        for connection_idx, rollup in enumerate(self._connection_rollups):
            received_at = int(aggregate.received_by_connection[connection_idx])
            if received_at >= 0:
                rollup.note_seen(
                    received_at_ns=received_at,
                    event_first_received_at_ns=aggregate.first_received_at_ns,
                    venue_timestamp_ns=aggregate.venue_timestamp_ns,
                    is_first_seen=aggregate.first_connection_index == connection_idx,
                    event_type=aggregate.event_type,
                )
            else:
                rollup.note_miss(
                    event_key=event_key,
                    received_at_ns=aggregate.first_received_at_ns,
                )


@dataclass(slots=True)
class ChartSeries:
    label: str
    values: list[Optional[float]]
    color: str


def build_comparative_insights(topology_summaries: dict[str, Any]) -> dict[str, Any]:
    if not topology_summaries:
        return {}

    topology_ids = sorted(topology_summaries, key=int)
    baseline_id = topology_ids[0]
    largest_id = topology_ids[-1]
    baseline = topology_summaries[baseline_id]
    largest = topology_summaries[largest_id]

    def best_topology_id(metric_name: str, *, prefer_highest: bool) -> str:
        if prefer_highest:
            return sorted(
                topology_summaries.items(),
                key=lambda item: (-item[1].get(metric_name, 0.0), int(item[0])),
            )[0][0]
        return sorted(
            topology_summaries.items(),
            key=lambda item: (item[1].get(metric_name, 0.0), int(item[0])),
        )[0][0]

    return {
        "baseline_topology": int(baseline_id),
        "largest_topology": int(largest_id),
        "coverage_gain_vs_baseline": round(
            largest.get("coverage_rate", 0.0) - baseline.get("coverage_rate", 0.0),
            6,
        ),
        "relative_miss_reduction_vs_baseline": round(
            baseline.get("relative_miss_rate", 0.0) - largest.get("relative_miss_rate", 0.0),
            6,
        ),
        "first_seen_gain_vs_baseline": round(
            largest.get("first_seen_win_rate", 0.0) - baseline.get("first_seen_win_rate", 0.0),
            6,
        ),
        "largest_relative_gap_events_reduction_vs_baseline": (
            baseline.get("largest_relative_gap_events", 0)
            - largest.get("largest_relative_gap_events", 0)
        ),
        "largest_relative_gap_ms_reduction_vs_baseline": round_or_none(
            (baseline.get("largest_relative_gap_ms") or 0.0)
            - (largest.get("largest_relative_gap_ms") or 0.0),
            3,
        ),
        "best_coverage_topology": int(best_topology_id("coverage_rate", prefer_highest=True)),
        "best_first_seen_topology": int(
            best_topology_id("first_seen_win_rate", prefer_highest=True)
        ),
        "lowest_relative_miss_topology": int(
            best_topology_id("relative_miss_rate", prefer_highest=False)
        ),
        "lowest_gap_topology": int(
            best_topology_id("largest_relative_gap_events", prefer_highest=False)
        ),
    }


def build_warmup_evidence(
    *,
    warmup_summary: dict[str, Any],
    post_warmup_summary: dict[str, Any],
    compare_window_seconds: float,
) -> dict[str, Any]:
    topology_comparison: dict[str, Any] = {}
    warmup_topologies = warmup_summary.get("topologies", {})
    post_topologies = post_warmup_summary.get("topologies", {})
    for topology_id in sorted(set(warmup_topologies) | set(post_topologies), key=int):
        warmup_row = warmup_topologies.get(topology_id, {})
        post_row = post_topologies.get(topology_id, {})
        warmup_freshness_p95 = metric_from_distribution(
            warmup_row.get("freshness_ms", {}), "p95"
        )
        post_freshness_p95 = metric_from_distribution(
            post_row.get("freshness_ms", {}), "p95"
        )
        warmup_arrival_p95 = metric_from_distribution(
            warmup_row.get("arrival_delta_ms", {}), "p95"
        )
        post_arrival_p95 = metric_from_distribution(
            post_row.get("arrival_delta_ms", {}), "p95"
        )
        topology_comparison[topology_id] = {
            "warmup_observations": warmup_row.get("event_observations", 0),
            "post_warmup_observations": post_row.get("event_observations", 0),
            "warmup_topology_seen_event_count": warmup_row.get("seen_event_count", 0),
            "post_warmup_topology_seen_event_count": post_row.get("seen_event_count", 0),
            "warmup_freshness_p95_ms": warmup_freshness_p95,
            "post_warmup_freshness_p95_ms": post_freshness_p95,
            "freshness_p95_delta_ms": round_or_none(
                (warmup_freshness_p95 or 0.0) - (post_freshness_p95 or 0.0),
                3,
            )
            if warmup_freshness_p95 is not None and post_freshness_p95 is not None
            else None,
            "warmup_arrival_p95_ms": warmup_arrival_p95,
            "post_warmup_arrival_p95_ms": post_arrival_p95,
            "arrival_p95_delta_ms": round_or_none(
                (warmup_arrival_p95 or 0.0) - (post_arrival_p95 or 0.0),
                3,
            )
            if warmup_arrival_p95 is not None and post_arrival_p95 is not None
            else None,
            "warmup_duplicate_rate": warmup_row.get("intra_topology_dup_rate"),
            "post_warmup_duplicate_rate": post_row.get("intra_topology_dup_rate"),
            "duplicate_rate_delta": round_or_none(
                (warmup_row.get("intra_topology_dup_rate") or 0.0)
                - (post_row.get("intra_topology_dup_rate") or 0.0),
                6,
            ),
            "supports_warmup": bool(
                warmup_freshness_p95 is not None
                and post_freshness_p95 is not None
                and warmup_freshness_p95 > post_freshness_p95
            ),
        }

    return {
        "compare_window_seconds": compare_window_seconds,
        "warmup_phase": {
            "union_event_count": warmup_summary.get("scored_union_event_count", 0),
            "observations": warmup_summary.get("scored_observations", 0),
            "topologies": warmup_topologies,
        },
        "post_warmup_compare_phase": {
            "union_event_count": post_warmup_summary.get("scored_union_event_count", 0),
            "observations": post_warmup_summary.get("scored_observations", 0),
            "topologies": post_topologies,
        },
        "topology_comparison": topology_comparison,
    }


def format_metric_value(value: Optional[float], kind: str, *, compact: bool = False) -> str:
    if value is None:
        return "-"
    if kind == "percent":
        return f"{value:.1f}%"
    if kind == "ms":
        if compact and abs(value) >= 1000:
            return f"{value / 1000:.2f}s"
        return f"{value:.1f}ms"
    if kind == "count":
        return f"{int(round(value))}"
    return f"{value:.3f}"


def short_connection_label(connection_id: str) -> str:
    parts = connection_id.split("_")
    if len(parts) >= 4 and parts[0] == "topology" and parts[2] == "conn":
        return f"{parts[1]}/{parts[3]}"
    return connection_id


def render_grouped_bar_chart_svg(
    *,
    title: str,
    subtitle: str,
    categories: Sequence[str],
    series: Sequence[ChartSeries],
    value_kind: str,
) -> str:
    width = 920
    height = 360
    left = 78
    right = 24
    top = 60
    bottom = 72
    plot_width = width - left - right
    plot_height = height - top - bottom

    values = [
        value
        for row in series
        for value in row.values
        if value is not None and math.isfinite(value)
    ]
    if not categories or not values:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
            '<rect width="100%" height="100%" fill="#ffffff"/>'
            f'<text x="24" y="34" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="16" font-weight="600" fill="#0f172a">{html.escape(title)}</text>'
            f'<text x="24" y="54" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" fill="#64748b">{html.escape(subtitle or "No scored data available.")}</text>'
            "</svg>"
        )

    raw_max = max(values)
    raw_min = min(values)
    y_max = max(0.0, raw_max) * 1.12 if raw_max > 0 else 0.0
    y_min = min(0.0, raw_min) * 1.12 if raw_min < 0 else 0.0
    if y_max <= y_min:
        y_max = y_min + 1.0
    span = y_max - y_min

    def y_for_value(value: float) -> float:
        return top + plot_height - ((value - y_min) / span) * plot_height

    zero_y = y_for_value(0.0)

    def x_for_category(index: int) -> float:
        return left + (index + 0.5) * (plot_width / max(1, len(categories)))

    grid_lines: list[str] = []
    for step in range(6):
        fraction = step / 5
        value = y_min + span * (1 - fraction)
        y = top + plot_height * fraction
        grid_lines.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" stroke="#eef2f7" stroke-width="1"/>'
        )
        grid_lines.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="11" fill="#94a3b8">{html.escape(format_metric_value(value, value_kind, compact=True))}</text>'
        )

    legend_items: list[str] = []
    legend_x = left
    for row in series:
        legend_items.append(
            f'<rect x="{legend_x}" y="22" width="10" height="10" rx="2" fill="{row.color}"/>'
            f'<text x="{legend_x + 16}" y="31" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" fill="#475569">{html.escape(row.label)}</text>'
        )
        legend_x += 140

    group_width = plot_width / max(1, len(categories))
    series_count = max(1, len(series))
    bar_gap = 6
    bar_width = min(30.0, (group_width * 0.78 - (series_count - 1) * bar_gap) / series_count)
    bar_width = max(6.0, bar_width)

    bars: list[str] = []
    for category_index, category in enumerate(categories):
        group_center = x_for_category(category_index)
        total_bar_width = series_count * bar_width + (series_count - 1) * bar_gap
        group_left = group_center - total_bar_width / 2
        for series_index, row in enumerate(series):
            value = row.values[category_index] if category_index < len(row.values) else None
            if value is None or not math.isfinite(value):
                continue
            x = group_left + series_index * (bar_width + bar_gap)
            y_val = y_for_value(value)
            if value >= 0:
                bar_y = y_val
                bar_height = zero_y - y_val
                label_y = bar_y - 6
            else:
                bar_y = zero_y
                bar_height = y_val - zero_y
                label_y = y_val + 14
            bar_height = max(0.0, bar_height)
            bars.append(
                f'<rect x="{x:.2f}" y="{bar_y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" rx="3" fill="{row.color}" opacity="0.95"/>'
            )
            bars.append(
                f'<text x="{x + bar_width / 2:.2f}" y="{label_y:.2f}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="10" fill="#475569">{html.escape(format_metric_value(value, value_kind, compact=True))}</text>'
            )
        bars.append(
            f'<text x="{group_center:.2f}" y="{top + plot_height + 26:.2f}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" font-weight="600" fill="#0f172a">{html.escape(category)}</text>'
        )

    zero_line = (
        f'<line x1="{left}" y1="{zero_y:.2f}" x2="{left + plot_width}" y2="{zero_y:.2f}" stroke="#cbd5e1" stroke-width="1.25"/>'
        if y_min < 0
        else ""
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<text x="24" y="32" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="16" font-weight="600" fill="#0f172a">{html.escape(title)}</text>'
        f'<text x="24" y="52" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" fill="#64748b">{html.escape(subtitle)}</text>'
        + "".join(legend_items)
        + f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#cbd5e1" stroke-width="1"/>'
        + f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#cbd5e1" stroke-width="1"/>'
        + "".join(grid_lines)
        + zero_line
        + "".join(bars)
        + "</svg>"
    )


def render_small_multiples_svg(
    *,
    title: str,
    subtitle: str,
    categories: Sequence[str],
    metrics: Sequence[dict[str, Any]],
) -> str:
    """Render one mini bar chart per metric, laid out in a row.

    Each metric dict has keys: label, value_kind, color, values (list aligned with categories).
    Use when metrics have wildly different magnitudes or units so a shared y-axis fails.
    """
    metric_count = max(1, len(metrics))
    panel_width = 300
    panel_gap = 18
    header_height = 64
    bottom_pad = 44
    panel_height = 220
    width = 24 + metric_count * panel_width + (metric_count - 1) * panel_gap + 24
    height = header_height + panel_height + bottom_pad

    panel_top = header_height
    inner_top = panel_top + 36
    inner_bottom = panel_top + panel_height - 26
    plot_h = inner_bottom - inner_top

    body_parts: list[str] = [
        f'<text x="24" y="32" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="16" font-weight="600" fill="#0f172a">{html.escape(title)}</text>',
        f'<text x="24" y="52" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" fill="#64748b">{html.escape(subtitle)}</text>',
    ]

    for panel_index, metric in enumerate(metrics):
        panel_x = 24 + panel_index * (panel_width + panel_gap)
        inner_left = panel_x + 16
        inner_right = panel_x + panel_width - 16
        inner_width = inner_right - inner_left
        values = [
            v
            for v in metric.get("values", [])
            if v is not None and isinstance(v, (int, float)) and math.isfinite(v)
        ]
        value_kind = str(metric.get("value_kind", "count"))
        color = str(metric.get("color", CHART_COLORS[0]))
        label = str(metric.get("label", ""))

        body_parts.append(
            f'<rect x="{panel_x:.2f}" y="{panel_top:.2f}" width="{panel_width:.2f}" height="{panel_height:.2f}" rx="10" fill="#f8fafc" stroke="#e2e8f0"/>'
        )
        body_parts.append(
            f'<text x="{panel_x + 16:.2f}" y="{panel_top + 22:.2f}" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" font-weight="600" fill="#0f172a">{html.escape(label)}</text>'
        )

        if not values:
            body_parts.append(
                f'<text x="{panel_x + panel_width / 2:.2f}" y="{panel_top + panel_height / 2:.2f}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="12" fill="#94a3b8">no data</text>'
            )
            continue

        raw_max = max(values)
        raw_min = min(values)
        y_max = max(0.0, raw_max) * 1.15 if raw_max > 0 else 0.0
        y_min = min(0.0, raw_min) * 1.15 if raw_min < 0 else 0.0
        if y_max <= y_min:
            y_max = y_min + 1.0
        span = y_max - y_min

        def y_for(value: float, _top: float = inner_top, _h: float = plot_h, _ymin: float = y_min, _span: float = span) -> float:
            return _top + _h - ((value - _ymin) / _span) * _h

        zero_y = y_for(0.0)

        best_value: Optional[float] = None
        lower_is_better_metric = bool(metric.get("lower_is_better", False))
        finite_vals = [v for v in metric.get("values", []) if v is not None and isinstance(v, (int, float)) and math.isfinite(v)]
        if finite_vals:
            best_value = min(finite_vals) if lower_is_better_metric else max(finite_vals)

        category_count = max(1, len(categories))
        slot_width = inner_width / category_count
        bar_width = min(34.0, slot_width * 0.6)
        bar_width = max(10.0, bar_width)

        for cat_index, category in enumerate(categories):
            cat_center = inner_left + (cat_index + 0.5) * slot_width
            raw_value = metric.get("values", [])[cat_index] if cat_index < len(metric.get("values", [])) else None
            x = cat_center - bar_width / 2
            if raw_value is None or not isinstance(raw_value, (int, float)) or not math.isfinite(raw_value):
                body_parts.append(
                    f'<text x="{cat_center:.2f}" y="{inner_bottom + 18:.2f}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="11" fill="#475569">{html.escape(category)}</text>'
                )
                continue
            value = float(raw_value)
            y_val = y_for(value)
            if value >= 0:
                bar_y = y_val
                bar_h = zero_y - y_val
                label_y = bar_y - 5
            else:
                bar_y = zero_y
                bar_h = y_val - zero_y
                label_y = y_val + 12
            bar_h = max(0.0, bar_h)
            is_best = best_value is not None and math.isclose(value, float(best_value), rel_tol=1e-9, abs_tol=1e-9)
            fill = color
            stroke = "#0f172a" if is_best else "none"
            body_parts.append(
                f'<rect x="{x:.2f}" y="{bar_y:.2f}" width="{bar_width:.2f}" height="{bar_h:.2f}" rx="3" fill="{fill}" opacity="0.95" stroke="{stroke}" stroke-width="{1.4 if is_best else 0}"/>'
            )
            body_parts.append(
                f'<text x="{cat_center:.2f}" y="{label_y:.2f}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="10" font-weight="{600 if is_best else 500}" fill="{"#0f172a" if is_best else "#475569"}">{html.escape(format_metric_value(value, value_kind, compact=True))}</text>'
            )
            body_parts.append(
                f'<text x="{cat_center:.2f}" y="{inner_bottom + 18:.2f}" text-anchor="middle" font-family="Inter, system-ui, -apple-system, Segoe UI, Arial, sans-serif" font-size="11" fill="#475569">{html.escape(category)}</text>'
            )

        body_parts.append(
            f'<line x1="{inner_left:.2f}" y1="{zero_y:.2f}" x2="{inner_right:.2f}" y2="{zero_y:.2f}" stroke="#cbd5e1" stroke-width="1"/>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        + "".join(body_parts)
        + "</svg>"
    )


def iso_to_ns(value: str) -> int:
    parsed = parse_market_datetime(value)
    if parsed is None:
        raise ValueError(f"invalid datetime value: {value!r}")
    return int(parsed.timestamp() * 1_000_000_000)


def bucket_count_for_duration(duration_seconds: float) -> int:
    return max(180, min(720, int(max(duration_seconds, 1.0) * 2)))


def load_visualization_state(
    summary: dict[str, Any],
    events_path: Path,
) -> dict[str, Any]:
    run_metadata = summary["run_metadata"]
    run_started_ns = iso_to_ns(run_metadata["started_at"])
    run_ended_ns = iso_to_ns(run_metadata["ended_at"])
    duration_seconds = max(1e-9, (run_ended_ns - run_started_ns) / 1_000_000_000)
    bucket_count = bucket_count_for_duration(duration_seconds)
    bucket_ns = max(1, int((run_ended_ns - run_started_ns) / bucket_count))

    connection_order = sorted(
        summary["connections"],
        key=lambda connection_id: (
            int(summary["connections"][connection_id]["topology_id"]),
            connection_id,
        ),
    )
    row_index = {connection_id: idx for idx, connection_id in enumerate(connection_order)}

    total_counts = [
        [0 for _ in range(bucket_count)] for _ in range(len(connection_order))
    ]
    phase_counts = {
        "warmup": [
            [0 for _ in range(bucket_count)] for _ in range(len(connection_order))
        ],
        "post_warmup_compare": [
            [0 for _ in range(bucket_count)] for _ in range(len(connection_order))
        ],
        "steady": [
            [0 for _ in range(bucket_count)] for _ in range(len(connection_order))
        ],
    }
    aggregates: dict[str, dict[str, Any]] = {}
    non_zero_counts: list[float] = []
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            connection_id = payload.get("connection_id")
            row = row_index.get(str(connection_id))
            if row is not None:
                received_at_ns = int(payload.get("received_at_ns"))
                bucket = min(
                    bucket_count - 1,
                    max(0, int((received_at_ns - run_started_ns) / bucket_ns)),
                )
                total_counts[row][bucket] += 1
                phase = str(payload.get("phase_kind") or "steady")
                rows_by_phase = phase_counts.setdefault(
                    phase,
                    [[0 for _ in range(bucket_count)] for _ in range(len(connection_order))],
                )
                rows_by_phase[row][bucket] += 1
            if payload.get("in_warmup"):
                continue
            event_key = str(payload.get("event_key"))
            topology_id = str(payload.get("topology_id"))
            aggregate = aggregates.get(event_key)
            received_at_ns = int(payload.get("received_at_ns"))
            if aggregate is None:
                aggregate = {
                    "first_received_at_ns": received_at_ns,
                    "topologies": {topology_id},
                }
                aggregates[event_key] = aggregate
            else:
                if received_at_ns < aggregate["first_received_at_ns"]:
                    aggregate["first_received_at_ns"] = received_at_ns
                aggregate["topologies"].add(topology_id)
    for row_counts in total_counts:
        for count in row_counts:
            if count > 0:
                non_zero_counts.append(float(count))
    intensity_ceiling = percentile(non_zero_counts, 0.95) or 1.0
    return {
        "run_started_ns": run_started_ns,
        "run_ended_ns": run_ended_ns,
        "duration_seconds": duration_seconds,
        "bucket_count": bucket_count,
        "connection_order": connection_order,
        "total_counts": total_counts,
        "phase_counts": phase_counts,
        "intensity_ceiling": intensity_ceiling,
        "aggregates": aggregates,
    }


def render_connection_event_timeline_svg(
    *,
    summary: dict[str, Any],
    timeline_state: dict[str, Any],
) -> str:
    run_started_ns = int(timeline_state["run_started_ns"])
    run_ended_ns = int(timeline_state["run_ended_ns"])
    duration_seconds = float(timeline_state["duration_seconds"])
    bucket_count = int(timeline_state["bucket_count"])
    connection_order = list(timeline_state["connection_order"])
    row_index = {connection_id: idx for idx, connection_id in enumerate(connection_order)}
    total_counts = timeline_state["total_counts"]
    phase_counts = timeline_state["phase_counts"]
    intensity_ceiling = float(timeline_state["intensity_ceiling"])

    width = 1140
    height = max(340, 110 + len(connection_order) * 26)
    left = 180
    right = 28
    top = 72
    bottom = 42
    plot_width = width - left - right
    row_height = 18
    row_gap = 8
    plot_height = len(connection_order) * (row_height + row_gap)

    def x_for_bucket(bucket: int) -> float:
        return left + bucket * (plot_width / bucket_count)

    phase_palette = {
        "warmup": "#f59e0b",
        "post_warmup_compare": "#10b981",
        "steady": "#2563eb",
    }

    segment_lines: list[str] = []
    for segment in summary.get("market_segments", [])[1:]:
        started_at_iso = segment.get("started_at_iso")
        if not started_at_iso:
            continue
        started_ns = iso_to_ns(started_at_iso)
        x = left + ((started_ns - run_started_ns) / max(1, run_ended_ns - run_started_ns)) * plot_width
        segment_lines.append(
            f'<line x1="{x:.2f}" y1="{top - 8}" x2="{x:.2f}" y2="{top + plot_height + 6}" stroke="#9ca3af" stroke-dasharray="5 5" stroke-width="1.2"/>'
        )

    rows: list[str] = []
    for connection_id, row in row_index.items():
        y = top + row * (row_height + row_gap)
        summary_row = summary["connections"][connection_id]
        rows.append(
            f'<text x="{left - 12}" y="{y + 13:.2f}" text-anchor="end" font-family="Arial, sans-serif" font-size="12" fill="#111827">{html.escape(short_connection_label(connection_id))}</text>'
        )
        rows.append(
            f'<rect x="{left}" y="{y:.2f}" width="{plot_width:.2f}" height="{row_height:.2f}" fill="#f8fafc" rx="4"/>'
        )
        for bucket, count in enumerate(total_counts[row]):
            if count <= 0:
                continue
            dominant_phase = "steady"
            dominant_count = -1
            for phase_name, rows_by_phase in phase_counts.items():
                phase_count = rows_by_phase[row][bucket]
                if phase_count > dominant_count:
                    dominant_phase = phase_name
                    dominant_count = phase_count
            opacity = 0.18 + 0.72 * min(1.0, count / max(1.0, intensity_ceiling))
            x = x_for_bucket(bucket)
            cell_width = max(1.0, plot_width / bucket_count)
            rows.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{cell_width:.2f}" height="{row_height:.2f}" fill="{phase_palette.get(dominant_phase, "#2563eb")}" opacity="{opacity:.3f}"/>'
            )
        rows.append(
            f'<text x="{left + plot_width + 10}" y="{y + 13:.2f}" font-family="Arial, sans-serif" font-size="11" fill="#6b7280">cov {summary_row.get("coverage_rate", 0.0) * 100:.1f}%</text>'
        )

    axis_labels: list[str] = []
    tick_count = 6
    for tick in range(tick_count + 1):
        fraction = tick / tick_count
        elapsed_seconds = duration_seconds * fraction
        x = left + plot_width * fraction
        axis_labels.append(
            f'<line x1="{x:.2f}" y1="{top + plot_height + 3}" x2="{x:.2f}" y2="{top + plot_height + 9}" stroke="#9ca3af" stroke-width="1"/>'
        )
        axis_labels.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 26:.2f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#6b7280">{html.escape(format_elapsed(elapsed_seconds))}</text>'
        )

    legend = []
    legend_x = left
    for label, color in (
        ("Warmup", "#f59e0b"),
        ("Post-Warmup", "#10b981"),
        ("Steady", "#2563eb"),
        ("Rebind", "#9ca3af"),
    ):
        if label == "Rebind":
            legend.append(
                f'<line x1="{legend_x}" y1="26" x2="{legend_x + 14}" y2="26" stroke="{color}" stroke-dasharray="4 4" stroke-width="2"/>'
                f'<text x="{legend_x + 22}" y="30" font-family="Arial, sans-serif" font-size="13" fill="#374151">{label}</text>'
            )
        else:
            legend.append(
                f'<rect x="{legend_x}" y="18" width="14" height="14" rx="3" fill="{color}"/>'
                f'<text x="{legend_x + 22}" y="30" font-family="Arial, sans-serif" font-size="13" fill="#374151">{label}</text>'
            )
        legend_x += 155

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Per-Connection Event Timeline">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        '<text x="32" y="42" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#111827">Per-Connection Event Timeline</text>'
        '<text x="32" y="66" font-family="Arial, sans-serif" font-size="14" fill="#6b7280">Each row is one websocket. Colored cells mean events arrived in that time bucket; white space means silence. Dashed lines mark market rebinds.</text>'
        + "".join(legend)
        + f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#9ca3af" stroke-width="1.25"/>'
        + "".join(segment_lines)
        + "".join(rows)
        + "".join(axis_labels)
        + "</svg>"
    )


def build_topology_gap_intervals(
    *,
    aggregates: dict[str, dict[str, Any]],
    topology_ids: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(
        aggregates.values(),
        key=lambda row: (row["first_received_at_ns"], len(row["topologies"])),
    )
    intervals: dict[str, list[dict[str, Any]]] = {topology_id: [] for topology_id in topology_ids}

    for topology_id in topology_ids:
        start_ns: Optional[int] = None
        end_ns: Optional[int] = None
        count = 0
        for aggregate in ordered:
            if topology_id not in aggregate["topologies"]:
                if start_ns is None:
                    start_ns = aggregate["first_received_at_ns"]
                end_ns = aggregate["first_received_at_ns"]
                count += 1
                continue
            if start_ns is not None and end_ns is not None and count > 0:
                intervals[topology_id].append(
                    {
                        "start_ns": start_ns,
                        "end_ns": end_ns,
                        "count": count,
                    }
                )
            start_ns = None
            end_ns = None
            count = 0
        if start_ns is not None and end_ns is not None and count > 0:
            intervals[topology_id].append(
                {
                    "start_ns": start_ns,
                    "end_ns": end_ns,
                    "count": count,
                }
            )
    return intervals


def render_topology_gap_timeline_svg(
    *,
    summary: dict[str, Any],
    gap_intervals: dict[str, list[dict[str, Any]]],
) -> str:
    run_metadata = summary["run_metadata"]
    run_started_ns = iso_to_ns(run_metadata["started_at"])
    run_ended_ns = iso_to_ns(run_metadata["ended_at"])
    width = 1140
    topology_ids = sorted(summary["topologies"], key=int)
    height = max(300, 110 + len(topology_ids) * 50)
    left = 120
    right = 28
    top = 72
    bottom = 44
    plot_width = width - left - right
    row_height = 22
    row_gap = 22
    plot_height = len(topology_ids) * (row_height + row_gap)
    run_span_ns = max(1, run_ended_ns - run_started_ns)

    all_counts = [
        interval["count"]
        for intervals in gap_intervals.values()
        for interval in intervals
    ]
    count_ceiling = percentile([float(count) for count in all_counts], 0.95) or 1.0

    segment_lines: list[str] = []
    for segment in summary.get("market_segments", [])[1:]:
        started_at_iso = segment.get("started_at_iso")
        if not started_at_iso:
            continue
        started_ns = iso_to_ns(started_at_iso)
        x = left + ((started_ns - run_started_ns) / run_span_ns) * plot_width
        segment_lines.append(
            f'<line x1="{x:.2f}" y1="{top - 8}" x2="{x:.2f}" y2="{top + plot_height + 6}" stroke="#9ca3af" stroke-dasharray="5 5" stroke-width="1.2"/>'
        )

    rows: list[str] = []
    for row_index, topology_id in enumerate(topology_ids):
        y = top + row_index * (row_height + row_gap)
        summary_row = summary["topologies"][topology_id]
        rows.append(
            f'<text x="{left - 12}" y="{y + 15:.2f}" text-anchor="end" font-family="Arial, sans-serif" font-size="13" fill="#111827">{html.escape(topology_id)} ws</text>'
        )
        rows.append(
            f'<rect x="{left}" y="{y:.2f}" width="{plot_width:.2f}" height="{row_height:.2f}" fill="#f8fafc" rx="4"/>'
        )
        for interval in gap_intervals.get(topology_id, []):
            start_x = left + ((interval["start_ns"] - run_started_ns) / run_span_ns) * plot_width
            end_x = left + ((interval["end_ns"] - run_started_ns) / run_span_ns) * plot_width
            rect_width = max(2.0, end_x - start_x)
            opacity = 0.18 + 0.72 * min(1.0, interval["count"] / max(1.0, count_ceiling))
            rows.append(
                f'<rect x="{start_x:.2f}" y="{y:.2f}" width="{rect_width:.2f}" height="{row_height:.2f}" fill="#dc2626" opacity="{opacity:.3f}" rx="3"/>'
            )
        rows.append(
            f'<text x="{left + plot_width + 12}" y="{y + 15:.2f}" font-family="Arial, sans-serif" font-size="11" fill="#6b7280">largest gap {summary_row.get("largest_relative_gap_events", 0)}</text>'
        )

    axis_labels: list[str] = []
    tick_count = 6
    duration_seconds = (run_ended_ns - run_started_ns) / 1_000_000_000
    for tick in range(tick_count + 1):
        fraction = tick / tick_count
        x = left + plot_width * fraction
        axis_labels.append(
            f'<line x1="{x:.2f}" y1="{top + plot_height + 3}" x2="{x:.2f}" y2="{top + plot_height + 9}" stroke="#9ca3af" stroke-width="1"/>'
        )
        axis_labels.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 26:.2f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#6b7280">{html.escape(format_elapsed(duration_seconds * fraction))}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Topology Gap Timeline">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        '<text x="32" y="42" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#111827">Topology Gap Timeline</text>'
        '<text x="32" y="66" font-family="Arial, sans-serif" font-size="14" fill="#6b7280">Red spans are topology-relative missed-event runs. Shorter and fewer spans mean the parallel websocket set is catching more of the union stream.</text>'
        + f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#9ca3af" stroke-width="1.25"/>'
        + "".join(segment_lines)
        + "".join(rows)
        + "".join(axis_labels)
        + "</svg>"
    )


def format_transition(
    before: Optional[float],
    after: Optional[float],
    kind: str,
    *,
    compact: bool = True,
) -> str:
    return (
        f"{format_metric_value(before, kind, compact=compact)}"
        f" → "
        f"{format_metric_value(after, kind, compact=compact)}"
    )


def lower_is_better(before: Optional[float], after: Optional[float]) -> bool:
    return before is not None and after is not None and after < before


def median_or_none(values: Sequence[Optional[float]]) -> Optional[float]:
    filtered = [float(value) for value in values if value is not None]
    return statistics.median(filtered) if filtered else None


def build_report_answers(
    *,
    topologies: Sequence[tuple[str, dict[str, Any]]],
    connections: Sequence[tuple[str, dict[str, Any]]],
    warmup_evidence: dict[str, Any],
) -> list[dict[str, str]]:
    if not topologies:
        return []

    baseline_id, baseline_row = topologies[0]
    largest_id, largest_row = topologies[-1]
    comparison = warmup_evidence.get("topology_comparison", {})
    warmup_phase_topologies = warmup_evidence.get("warmup_phase", {}).get("topologies", {})
    post_phase_topologies = warmup_evidence.get("post_warmup_compare_phase", {}).get("topologies", {})
    warmup_topology_ids = sorted(
        set(comparison) | set(warmup_phase_topologies) | set(post_phase_topologies),
        key=int,
    )

    latency_support_ids = [
        topology_id
        for topology_id in warmup_topology_ids
        if lower_is_better(
            comparison.get(topology_id, {}).get("warmup_arrival_p95_ms"),
            comparison.get(topology_id, {}).get("post_warmup_arrival_p95_ms"),
        )
        and lower_is_better(
            comparison.get(topology_id, {}).get("warmup_freshness_p95_ms"),
            comparison.get(topology_id, {}).get("post_warmup_freshness_p95_ms"),
        )
    ]
    gap_support_ids = [
        topology_id
        for topology_id in warmup_topology_ids
        if lower_is_better(
            warmup_phase_topologies.get(topology_id, {}).get("relative_miss_rate"),
            post_phase_topologies.get(topology_id, {}).get("relative_miss_rate"),
        )
        and lower_is_better(
            warmup_phase_topologies.get(topology_id, {}).get("largest_relative_gap_events"),
            post_phase_topologies.get(topology_id, {}).get("largest_relative_gap_events"),
        )
    ]

    larger_topologies = list(topologies[1:])
    larger_topology_count = len(larger_topologies)
    quality_improvement_ids = [
        topology_id
        for topology_id, row in larger_topologies
        if row.get("coverage_rate", 0.0) > baseline_row.get("coverage_rate", 0.0)
        and row.get("relative_miss_rate", 1.0) < baseline_row.get("relative_miss_rate", 1.0)
    ]
    gap_improvement_ids = [
        topology_id
        for topology_id, row in larger_topologies
        if row.get("relative_gap_runs", math.inf) < baseline_row.get("relative_gap_runs", math.inf)
        and row.get("largest_relative_gap_events", math.inf)
        < baseline_row.get("largest_relative_gap_events", math.inf)
    ]
    latency_improvement_ids = [
        topology_id
        for topology_id, row in larger_topologies
        if lower_is_better(
            metric_from_distribution(baseline_row.get("arrival_delta_ms", {}), "p95"),
            metric_from_distribution(row.get("arrival_delta_ms", {}), "p95"),
        )
        and lower_is_better(
            metric_from_distribution(baseline_row.get("freshness_ms", {}), "p95"),
            metric_from_distribution(row.get("freshness_ms", {}), "p95"),
        )
    ]

    best_coverage_id, best_coverage_row = sorted(
        topologies,
        key=lambda item: (-item[1].get("coverage_rate", 0.0), int(item[0])),
    )[0]
    lowest_miss_id, lowest_miss_row = sorted(
        topologies,
        key=lambda item: (item[1].get("relative_miss_rate", 1.0), int(item[0])),
    )[0]
    best_arrival_id, best_arrival_row = sorted(
        topologies,
        key=lambda item: (
            metric_from_distribution(item[1].get("arrival_delta_ms", {}), "p95") or math.inf,
            int(item[0]),
        ),
    )[0]
    best_freshness_id, best_freshness_row = sorted(
        topologies,
        key=lambda item: (
            metric_from_distribution(item[1].get("freshness_ms", {}), "p95") or math.inf,
            int(item[0]),
        ),
    )[0]

    connections_by_topology: dict[str, list[dict[str, Any]]] = {}
    for _, row in connections:
        topology_id = str(row.get("topology_id"))
        connections_by_topology.setdefault(topology_id, []).append(row)

    largest_topology_connections = connections_by_topology.get(largest_id, [])
    largest_topology_stalled_connections = sum(
        1
        for row in largest_topology_connections
        if (row.get("relative_gap_runs") or 0) > 0
    )
    largest_topology_worst_socket_gap = max(
        (
            int(row.get("largest_relative_gap_events") or 0)
            for row in largest_topology_connections
        ),
        default=0,
    )

    answers: list[dict[str, str]] = []
    if warmup_topology_ids:
        answers.append(
            {
                "question": "Does warmup have any positive effect on data gaps and latency?",
                "verdict": "No clear effect",
                "tone": "mixed",
                "summary": (
                    f"Only {len(latency_support_ids)}/{len(warmup_topology_ids)} topologies improved on both arrival and freshness after warmup, "
                    f"and only {len(gap_support_ids)}/{len(warmup_topology_ids)} improved on both miss rate and largest-gap size."
                ),
                "detail": "In this run, the immediate post-warmup window was not consistently cleaner than the warmup window.",
            }
        )

    answers.append(
        {
            "question": "Does more connections improve data quality, gaps, and latency?",
            "verdict": "Mostly yes" if larger_topology_count > 0 else "Not enough comparison data",
            "tone": (
                "yes"
                if larger_topology_count > 0 and len(latency_improvement_ids) == larger_topology_count
                else "mixed"
            ),
            "summary": (
                (
                    f"{len(quality_improvement_ids)}/{larger_topology_count} larger topologies beat {baseline_id} ws on both coverage and miss rate, "
                    f"and {len(gap_improvement_ids)}/{larger_topology_count} beat it on both gap runs and largest-gap size. "
                    f"{best_coverage_id} ws had the best coverage "
                    f"({format_metric_value(best_coverage_row.get('coverage_rate', 0.0) * 100, 'percent')}), "
                    f"{lowest_miss_id} ws had the lowest miss rate "
                    f"({format_metric_value(lowest_miss_row.get('relative_miss_rate', 0.0) * 100, 'percent')}), "
                    f"{best_arrival_id} ws had the best arrival P95 "
                    f"({format_metric_value(metric_from_distribution(best_arrival_row.get('arrival_delta_ms', {}), 'p95'), 'ms', compact=True)}), "
                    f"and {best_freshness_id} ws had the best freshness P95 "
                    f"({format_metric_value(metric_from_distribution(best_freshness_row.get('freshness_ms', {}), 'p95'), 'ms', compact=True)})."
                    if larger_topology_count > 0
                    else "This run only has one topology, so there is no scaling comparison."
                )
            ),
            "detail": (
                (
                    f"Latency was not monotonic: {len(latency_improvement_ids)}/{larger_topology_count} larger topologies "
                    f"beat {baseline_id} ws on both arrival and freshness."
                    if larger_topology_count > 0
                    else "Add at least one larger topology if you want the report to answer scaling questions."
                )
            ),
        }
    )

    answers.append(
        {
            "question": "Does more connections help prevent stalled connections (data gaps)?",
            "verdict": "Yes, at the pool level" if larger_topology_count > 0 else "Not enough comparison data",
            "tone": "yes" if larger_topology_count > 0 else "mixed",
            "summary": (
                (
                    f"Topology gap runs fell from {baseline_row.get('relative_gap_runs', 0)} at {baseline_id} ws to {largest_row.get('relative_gap_runs', 0)} at {largest_id} ws, "
                    f"and the largest topology-level gap fell from {baseline_row.get('largest_relative_gap_events', 0)} to {largest_row.get('largest_relative_gap_events', 0)} events."
                    if larger_topology_count > 0
                    else "This run only has one topology, so it cannot answer whether adding connections helps."
                )
            ),
            "detail": (
                f"Redundancy did not stop sockets from stalling individually: {largest_topology_stalled_connections}/{len(largest_topology_connections)} sockets "
                f"in the {largest_id}-ws pool still had gap runs, and the worst single socket missed {largest_topology_worst_socket_gap} events in one gap."
            ),
        }
    )
    return answers


def render_report_html(
    summary: dict[str, Any],
    chart_assets: dict[str, dict[str, str]],
) -> str:
    run_metadata = summary["run_metadata"]
    topologies = sorted(summary["topologies"].items(), key=lambda item: int(item[0]))
    connections = sorted(summary["connections"].items())
    activity_window = summary.get("activity_window", {})
    warmup_evidence = summary.get("warmup_evidence", {})
    comparative_insights = summary.get("comparative_insights", {})
    caveats = summary.get("caveats", []) or []

    report_answers = build_report_answers(
        topologies=topologies,
        connections=connections,
        warmup_evidence=warmup_evidence,
    )
    answer_cards_html = "".join(
        "<article class='answer-card'>"
        f"<div class='answer-meta'>Q{index} · <span class='verdict-chip verdict-{html.escape(answer['tone'])}'>{html.escape(answer['verdict'])}</span></div>"
        f"<h3>{html.escape(answer['question'])}</h3>"
        f"<p class='answer-body'>{html.escape(answer['summary'])}</p>"
        f"<p class='answer-foot'>{html.escape(answer['detail'])}</p>"
        "</article>"
        for index, answer in enumerate(report_answers, start=1)
    )

    def best_in(metric_key: str, *, lower_is_better_flag: bool, dist_field: Optional[str] = None) -> Optional[str]:
        candidates: list[tuple[str, float]] = []
        for topology_id, row in topologies:
            if dist_field is not None:
                raw = metric_from_distribution(row.get(metric_key, {}), dist_field)
            else:
                raw = row.get(metric_key)
            if raw is None:
                continue
            try:
                candidates.append((topology_id, float(raw)))
            except (TypeError, ValueError):
                continue
        if not candidates:
            return None
        chosen = min(candidates, key=lambda item: item[1]) if lower_is_better_flag else max(candidates, key=lambda item: item[1])
        return chosen[0]

    best_by_metric = {
        "coverage": best_in("coverage_rate", lower_is_better_flag=False),
        "miss": best_in("relative_miss_rate", lower_is_better_flag=True),
        "first_seen": best_in("first_seen_win_rate", lower_is_better_flag=False),
        "arrival": best_in("arrival_delta_ms", lower_is_better_flag=True, dist_field="p95"),
        "freshness": best_in("freshness_ms", lower_is_better_flag=True, dist_field="p95"),
        "gap_runs": best_in("relative_gap_runs", lower_is_better_flag=True),
        "largest_gap_events": best_in("largest_relative_gap_events", lower_is_better_flag=True),
        "largest_gap_duration": best_in("largest_relative_gap_ms", lower_is_better_flag=True),
    }

    def cell(value_html: str, topology_id: str, metric_key: str) -> str:
        winner = best_by_metric.get(metric_key)
        css = "cell"
        if winner is not None and str(topology_id) == str(winner):
            css += " cell-best"
        return f"<td class='{css}'>{value_html}</td>"

    topology_rows_html: list[str] = []
    for topology_id, row in topologies:
        topology_rows_html.append(
            "<tr>"
            f"<td class='cell cell-label'>{html.escape(topology_id)} ws</td>"
            + cell(format_metric_value(row.get('coverage_rate', 0.0) * 100, 'percent'), topology_id, 'coverage')
            + cell(format_metric_value(row.get('relative_miss_rate', 0.0) * 100, 'percent'), topology_id, 'miss')
            + cell(format_metric_value(row.get('first_seen_win_rate', 0.0) * 100, 'percent'), topology_id, 'first_seen')
            + cell(format_metric_value(metric_from_distribution(row.get('arrival_delta_ms', {}), 'p95'), 'ms', compact=True), topology_id, 'arrival')
            + cell(format_metric_value(metric_from_distribution(row.get('freshness_ms', {}), 'p95'), 'ms', compact=True), topology_id, 'freshness')
            + cell(str(row.get('relative_gap_runs', 0)), topology_id, 'gap_runs')
            + cell(str(row.get('largest_relative_gap_events', 0)), topology_id, 'largest_gap_events')
            + cell(format_metric_value(row.get('largest_relative_gap_ms'), 'ms', compact=True), topology_id, 'largest_gap_duration')
            + "</tr>"
        )

    def transition_cell(
        before: Optional[float],
        after: Optional[float],
        kind: str,
    ) -> str:
        before_s = format_metric_value(before, kind, compact=True)
        after_s = format_metric_value(after, kind, compact=True)
        if before is None or after is None:
            arrow_html = "<span class='trend trend-flat'>→</span>"
        elif after < before:
            arrow_html = "<span class='trend trend-down'>▼</span>"
        elif after > before:
            arrow_html = "<span class='trend trend-up'>▲</span>"
        else:
            arrow_html = "<span class='trend trend-flat'>→</span>"
        return (
            "<td class='cell cell-transition'>"
            "<div class='transition-inner'>"
            f"<span class='transition-before'>{html.escape(before_s)}</span>"
            f"{arrow_html}"
            f"<span class='transition-after'>{html.escape(after_s)}</span>"
            "</div>"
            "</td>"
        )

    warmup_rows_html: list[str] = []
    warmup_phase_topologies = warmup_evidence.get("warmup_phase", {}).get("topologies", {})
    post_phase_topologies = warmup_evidence.get("post_warmup_compare_phase", {}).get("topologies", {})
    warmup_topology_ids = sorted(
        set(warmup_phase_topologies) | set(post_phase_topologies),
        key=int,
    )
    for topology_id in warmup_topology_ids:
        warmup_row = warmup_phase_topologies.get(topology_id, {})
        post_row = post_phase_topologies.get(topology_id, {})
        before_miss = warmup_row.get("relative_miss_rate")
        after_miss = post_row.get("relative_miss_rate")
        warmup_rows_html.append(
            "<tr>"
            f"<td class='cell cell-label'>{html.escape(topology_id)} ws</td>"
            + transition_cell(
                (before_miss or 0.0) * 100 if before_miss is not None else None,
                (after_miss or 0.0) * 100 if after_miss is not None else None,
                "percent",
            )
            + transition_cell(
                warmup_row.get("largest_relative_gap_events"),
                post_row.get("largest_relative_gap_events"),
                "count",
            )
            + transition_cell(
                metric_from_distribution(warmup_row.get("arrival_delta_ms", {}), "p95"),
                metric_from_distribution(post_row.get("arrival_delta_ms", {}), "p95"),
                "ms",
            )
            + transition_cell(
                metric_from_distribution(warmup_row.get("freshness_ms", {}), "p95"),
                metric_from_distribution(post_row.get("freshness_ms", {}), "p95"),
                "ms",
            )
            + "</tr>"
        )

    connections_by_topology: dict[str, list[dict[str, Any]]] = {}
    for _, row in connections:
        topology_id = str(row.get("topology_id"))
        connections_by_topology.setdefault(topology_id, []).append(row)

    stall_rows_html: list[str] = []
    for topology_id, rows in sorted(connections_by_topology.items(), key=lambda item: int(item[0])):
        stalled = sum(1 for row in rows if (row.get("relative_gap_runs") or 0) > 0)
        worst_socket_gap = max(((row.get("largest_relative_gap_events") or 0) for row in rows), default=0)
        stall_rows_html.append(
            "<tr>"
            f"<td class='cell cell-label'>{html.escape(topology_id)} ws</td>"
            f"<td class='cell'>{len(rows)}</td>"
            f"<td class='cell'>{stalled}/{len(rows)}</td>"
            f"<td class='cell'>{format_metric_value(median_or_none([row.get('relative_gap_runs') for row in rows]), 'count')}</td>"
            f"<td class='cell'>{worst_socket_gap}</td>"
            f"<td class='cell'>{format_metric_value(median_or_none([metric_from_distribution(row.get('arrival_delta_ms', {}), 'p95') for row in rows]), 'ms', compact=True)}</td>"
            f"<td class='cell'>{format_metric_value(median_or_none([metric_from_distribution(row.get('freshness_ms', {}), 'p95') for row in rows]), 'ms', compact=True)}</td>"
            "</tr>"
        )

    def kpi_tile(
        *,
        label: str,
        value: str,
        detail: str,
        tone: str = "neutral",
    ) -> str:
        return (
            f"<div class='kpi kpi-{html.escape(tone)}'>"
            f"<div class='kpi-label'>{html.escape(label)}</div>"
            f"<div class='kpi-value'>{html.escape(value)}</div>"
            f"<div class='kpi-detail'>{html.escape(detail)}</div>"
            "</div>"
        )

    def format_topology_label(topology_id: Optional[str]) -> str:
        return f"{topology_id} ws" if topology_id else "—"

    best_cov_id = best_by_metric.get("coverage")
    best_cov_val = (
        (dict(topologies).get(best_cov_id, {}).get("coverage_rate") or 0.0) * 100
        if best_cov_id is not None
        else 0.0
    )
    best_fresh_id = best_by_metric.get("freshness")
    best_fresh_val = (
        metric_from_distribution(dict(topologies).get(best_fresh_id, {}).get("freshness_ms", {}), "p95")
        if best_fresh_id is not None
        else None
    )
    best_arrival_id = best_by_metric.get("arrival")
    best_arrival_val = (
        metric_from_distribution(dict(topologies).get(best_arrival_id, {}).get("arrival_delta_ms", {}), "p95")
        if best_arrival_id is not None
        else None
    )

    topology_map = dict(topologies)
    baseline_id = str(topologies[0][0]) if topologies else ""
    baseline_gap = topology_map.get(baseline_id, {}).get("largest_relative_gap_events", 0) if baseline_id else 0
    largest_id = str(topologies[-1][0]) if topologies else ""
    largest_gap = topology_map.get(largest_id, {}).get("largest_relative_gap_events", 0) if largest_id else 0

    gap_reduction_kpi_detail = (
        f"1 ws → {largest_id} ws: {baseline_gap} → {largest_gap} events"
        if topologies
        else "—"
    )

    kpis_html = "".join(
        [
            kpi_tile(
                label="Best Coverage",
                value=format_metric_value(best_cov_val, "percent"),
                detail=format_topology_label(best_cov_id),
                tone="good",
            ),
            kpi_tile(
                label="Best Freshness P95",
                value=format_metric_value(best_fresh_val, "ms", compact=True),
                detail=format_topology_label(best_fresh_id),
                tone="good",
            ),
            kpi_tile(
                label="Best Arrival P95",
                value=format_metric_value(best_arrival_val, "ms", compact=True),
                detail=format_topology_label(best_arrival_id),
                tone="good",
            ),
            kpi_tile(
                label="Largest Gap Reduction",
                value=f"−{baseline_gap - largest_gap}" if topologies else "—",
                detail=gap_reduction_kpi_detail,
                tone="accent",
            ),
        ]
    )

    chart_card_specs: list[tuple[str, str, str, str]] = [
        ("topology_performance", "Data Quality", "How coverage, first-seen wins, and relative miss change as the pool scales.", "coverage"),
        ("topology_latency", "Latency & Freshness", "Per-topology p50/p95 — lower is better.", "latency"),
        ("topology_gap_counts", "Relative Gap Counts", "Each metric scaled independently so small values stay readable.", "gap"),
        ("topology_gap_durations", "Relative Gap Durations", "How long topology-relative gaps last.", "duration"),
        ("warmup_quality", "Warmup vs Post-Warmup Freshness", "Warmup window compared against the first stable window after connect/rebind.", "warmup"),
        ("connection_outliers", "Connection Outliers", "The 12 worst sockets by freshness P95 — shows the long tail behind pool averages.", "outliers"),
    ]

    def chart_card(name: str, heading: str, description: str) -> str:
        if name not in chart_assets:
            return ""
        return (
            "<article class='chart-card'>"
            f"<header class='chart-card-header'><h3>{html.escape(heading)}</h3><p>{html.escape(description)}</p></header>"
            f"<div class='chart-body'>{chart_assets[name]['svg']}</div>"
            "</article>"
        )

    primary_charts_html = "".join(
        chart_card(name, heading, desc)
        for name, heading, desc, _ in chart_card_specs[:5]
    )
    supporting_charts_html = "".join(
        chart_card(name, heading, desc)
        for name, heading, desc, _ in chart_card_specs[5:]
    )

    extra_chart_names = [
        name
        for name in chart_assets
        if name not in {spec[0] for spec in chart_card_specs}
    ]
    for name in extra_chart_names:
        heading = chart_assets[name].get("title") or name.replace("_", " ").title()
        desc = chart_assets[name].get("subtitle") or ""
        supporting_charts_html += chart_card(name, heading, desc)

    caveats_html = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in caveats
    )
    caveats_block = (
        f"<section class='panel caveats'><h3>Caveats</h3><ul>{caveats_html}</ul></section>"
        if caveats_html
        else ""
    )

    topologies_label = ", ".join(map(str, run_metadata.get("topologies", [])))
    market_slug = html.escape(str(run_metadata.get("market_slug")))
    series_id = html.escape(str(run_metadata.get("series_id")))
    duration_seconds = run_metadata.get("duration_seconds")
    warmup_seconds = run_metadata.get("warmup_seconds")
    compare_seconds = run_metadata.get("warmup_compare_window_seconds")
    tail_silence = activity_window.get("run_tail_silence_seconds")
    output_dir_label = html.escape(str(run_metadata.get("output_dir")))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket CLOB WS Benchmark Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f1f5f9;
      --surface: #ffffff;
      --surface-muted: #f8fafc;
      --border: #e2e8f0;
      --border-strong: #cbd5e1;
      --text: #0f172a;
      --text-muted: #475569;
      --text-subtle: #64748b;
      --accent: #2563eb;
      --accent-soft: #dbeafe;
      --good: #059669;
      --good-soft: #d1fae5;
      --warn: #b45309;
      --warn-soft: #fef3c7;
      --bad: #dc2626;
      --bad-soft: #fee2e2;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
      font-size: 14px;
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 40px 32px 64px;
    }}
    a {{ color: var(--accent); }}
    h1, h2, h3 {{ margin: 0; color: var(--text); letter-spacing: -0.01em; }}
    h1 {{ font-size: 26px; font-weight: 700; }}
    h2 {{ font-size: 18px; font-weight: 600; }}
    h3 {{ font-size: 15px; font-weight: 600; }}
    p {{ margin: 0; color: var(--text-muted); }}
    .eyebrow {{
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 6px;
    }}
    .hero {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 32px;
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px 18px;
      color: var(--text-subtle);
      font-size: 13px;
    }}
    .hero-meta span strong {{ color: var(--text); font-weight: 600; }}
    .hero-note {{
      font-size: 12px;
      color: var(--text-subtle);
    }}
    .kpi-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
      margin-bottom: 32px;
    }}
    .kpi {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .kpi-label {{
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-subtle);
    }}
    .kpi-value {{
      font-size: 26px;
      font-weight: 700;
      color: var(--text);
      font-variant-numeric: tabular-nums;
    }}
    .kpi-detail {{
      font-size: 12px;
      color: var(--text-muted);
    }}
    .kpi-good .kpi-value {{ color: var(--good); }}
    .kpi-accent .kpi-value {{ color: var(--accent); }}
    .section {{
      margin-bottom: 32px;
    }}
    .section-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 12px;
      gap: 12px;
    }}
    .section-head h2 {{ margin: 0; }}
    .section-head p {{ margin: 0; font-size: 13px; color: var(--text-subtle); }}
    .answers {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 14px;
    }}
    .answer-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 18px 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .answer-card h3 {{ font-size: 15px; line-height: 1.35; color: var(--text); }}
    .answer-card .answer-body {{ font-size: 13px; color: var(--text-muted); }}
    .answer-card .answer-foot {{ font-size: 12px; color: var(--text-subtle); }}
    .answer-meta {{ font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-subtle); }}
    .verdict-chip {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      letter-spacing: 0.02em;
      font-weight: 600;
    }}
    .verdict-yes {{ background: var(--good-soft); color: var(--good); }}
    .verdict-mixed {{ background: var(--warn-soft); color: var(--warn); }}
    .verdict-no {{ background: var(--bad-soft); color: var(--bad); }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    thead th {{
      text-align: left;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--text-subtle);
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    tbody td.cell {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      color: var(--text);
      white-space: nowrap;
    }}
    tbody tr:last-child td.cell {{ border-bottom: none; }}
    tbody td.cell-label {{ font-weight: 600; }}
    tbody td.cell-best {{
      color: var(--good);
      font-weight: 600;
      background: var(--good-soft);
    }}
    .cell-transition {{
      font-variant-numeric: tabular-nums;
    }}
    .transition-inner {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .transition-before {{ color: var(--text-subtle); }}
    .transition-after {{ color: var(--text); font-weight: 600; }}
    .trend {{
      font-size: 11px;
      width: 18px;
      text-align: center;
    }}
    .trend-down {{ color: var(--good); }}
    .trend-up {{ color: var(--bad); }}
    .trend-flat {{ color: var(--text-subtle); }}
    .charts {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }}
    .chart-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .chart-card-header h3 {{ font-size: 14px; }}
    .chart-card-header p {{ font-size: 12px; color: var(--text-subtle); margin-top: 2px; }}
    .chart-body {{ width: 100%; }}
    .chart-body svg {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .caveats ul {{ margin: 8px 0 0; padding-left: 18px; color: var(--text-muted); font-size: 12px; }}
    .caveats li {{ margin-bottom: 4px; }}
    @media (max-width: 720px) {{
      .container {{ padding: 24px 16px 48px; }}
      .kpi-row {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
      .charts {{ grid-template-columns: 1fr; }}
      thead th, tbody td.cell {{ padding: 8px; font-size: 12px; }}
    }}
  </style>
</head>
<body>
  <main class="container">
    <header class="hero">
      <div class="eyebrow">Polymarket CLOB WS Benchmark</div>
      <h1>Topology scaling report</h1>
      <div class="hero-meta">
        <span>Market: <strong>{market_slug}</strong></span>
        <span>Series: <strong>{series_id}</strong></span>
        <span>Topologies: <strong>{html.escape(topologies_label)}</strong></span>
        <span>Duration: <strong>{duration_seconds}s</strong></span>
      </div>
      <p class="hero-note">Warmup {warmup_seconds}s per connection · compare window {compare_seconds}s · tail silence {tail_silence}s · output {output_dir_label}</p>
    </header>

    <section class="kpi-row">
      {kpis_html}
    </section>

    <section class="section">
      <div class="section-head">
        <h2>Headline questions</h2>
        <p>Verdicts distilled from topology-level evidence in this run.</p>
      </div>
      <div class="answers">
        {answer_cards_html}
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>Topology summary</h2>
        <p>Best value in each column is highlighted.</p>
      </div>
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>Topology</th>
              <th>Coverage</th>
              <th>Relative Miss</th>
              <th>First Seen</th>
              <th>Arrival P95</th>
              <th>Freshness P95</th>
              <th>Gap Runs</th>
              <th>Largest Gap Events</th>
              <th>Largest Gap Duration</th>
            </tr>
          </thead>
          <tbody>
            {''.join(topology_rows_html)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>Warmup vs post-warmup</h2>
        <p>Each cell compares the warmup window to the first stable window after connect/rebind. ▼ green means the metric improved.</p>
      </div>
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>Topology</th>
              <th>Miss Rate</th>
              <th>Largest Gap Events</th>
              <th>Arrival P95</th>
              <th>Freshness P95</th>
            </tr>
          </thead>
          <tbody>
            {''.join(warmup_rows_html)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>Single-socket stall evidence</h2>
        <p>Individual sockets can stall even when the pool looks healthy — this table shows the cost behind pool-level wins.</p>
      </div>
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>Topology</th>
              <th>Sockets</th>
              <th>Sockets with Gap Runs</th>
              <th>Median Gap Runs / Socket</th>
              <th>Worst Socket Gap Events</th>
              <th>Median Arrival P95</th>
              <th>Median Freshness P95</th>
            </tr>
          </thead>
          <tbody>
            {''.join(stall_rows_html)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>Charts</h2>
        <p>Each metric uses its own y-axis so small values stay readable alongside large ones.</p>
      </div>
      <div class="charts">
        {primary_charts_html}
      </div>
    </section>

    {"<section class='section'><div class='section-head'><h2>Supporting charts</h2><p>Where pool-level wins come from when individual sockets remain noisy.</p></div><div class='charts'>" + supporting_charts_html + "</div></section>" if supporting_charts_html else ""}

    {caveats_block}
  </main>
</body>
</html>
"""


def build_visualization_payload(
    summary: dict[str, Any],
    *,
    visualization_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    topology_ids = sorted(summary["topologies"], key=int)
    topology_labels = [f"{topology_id} ws" for topology_id in topology_ids]
    warmup_evidence = summary.get("warmup_evidence", {})
    warmup_comparison = warmup_evidence.get("topology_comparison", {})

    def topology_metric(metric_name: str) -> list[Optional[float]]:
        values: list[Optional[float]] = []
        for topology_id in topology_ids:
            raw_value = summary["topologies"][topology_id].get(metric_name)
            if raw_value is None:
                values.append(None)
            else:
                values.append(float(raw_value))
        return values

    def topology_dist(metric_name: str, field_name: str) -> list[Optional[float]]:
        return [
            metric_from_distribution(
                summary["topologies"][topology_id].get(metric_name, {}),
                field_name,
            )
            for topology_id in topology_ids
        ]

    payload: dict[str, Any] = {
        "topology_performance": {
            "title": "Topology Performance",
            "subtitle": "Coverage, first-seen share, and relative miss improve or regress as the websocket pool grows.",
            "value_kind": "percent",
            "categories": topology_labels,
            "series": [
                {
                    "label": "Coverage",
                    "color": CHART_COLORS[0],
                    "values": [value * 100 for value in topology_metric("coverage_rate")],
                },
                {
                    "label": "First Seen",
                    "color": CHART_COLORS[1],
                    "values": [value * 100 for value in topology_metric("first_seen_win_rate")],
                },
                {
                    "label": "Relative Miss",
                    "color": CHART_COLORS[2],
                    "values": [value * 100 for value in topology_metric("relative_miss_rate")],
                },
            ],
        },
        "topology_latency": {
            "title": "Latency and Freshness",
            "subtitle": "Lower is better. This shows which topology gets early copies and which one drifts stale.",
            "value_kind": "ms",
            "categories": topology_labels,
            "series": [
                {
                    "label": "Arrival P50",
                    "color": CHART_COLORS[0],
                    "values": topology_dist("arrival_delta_ms", "p50"),
                },
                {
                    "label": "Arrival P95",
                    "color": CHART_COLORS[1],
                    "values": topology_dist("arrival_delta_ms", "p95"),
                },
                {
                    "label": "Freshness P50",
                    "color": CHART_COLORS[3],
                    "values": topology_dist("freshness_ms", "p50"),
                },
                {
                    "label": "Freshness P95",
                    "color": CHART_COLORS[4],
                    "values": topology_dist("freshness_ms", "p95"),
                },
            ],
        },
        "topology_gap_counts": {
            "title": "Relative Gap Counts",
            "subtitle": "These are topology-relative gap runs, not authoritative venue loss.",
            "value_kind": "count",
            "categories": topology_labels,
            "series": [
                {
                    "label": "Gap Runs",
                    "color": CHART_COLORS[0],
                    "values": topology_metric("relative_gap_runs"),
                },
                {
                    "label": "Largest Gap Events",
                    "color": CHART_COLORS[2],
                    "values": topology_metric("largest_relative_gap_events"),
                },
                {
                    "label": "Relative Loss Events",
                    "color": CHART_COLORS[4],
                    "values": topology_metric("relative_loss_count"),
                },
            ],
        },
        "topology_gap_durations": {
            "title": "Relative Gap Durations",
            "subtitle": "Longer runs mean the topology went absent while other sockets were still seeing events.",
            "value_kind": "ms",
            "categories": topology_labels,
            "series": [
                {
                    "label": "Largest Gap",
                    "color": CHART_COLORS[0],
                    "values": topology_metric("largest_relative_gap_ms"),
                },
                {
                    "label": "Gap Duration P95",
                    "color": CHART_COLORS[1],
                    "values": topology_dist("relative_gap_duration_ms", "p95"),
                },
                {
                    "label": "Inter-Event Gap P95",
                    "color": CHART_COLORS[3],
                    "values": topology_dist("inter_event_gap_ms", "p95"),
                },
            ],
        },
        "warmup_quality": {
            "title": "Warmup Quality Check",
            "subtitle": "Compare warmup against the first stable window after each connect or market rebind.",
            "value_kind": "ms",
            "categories": topology_labels,
            "series": [
                {
                    "label": "Warmup Freshness P95",
                    "color": CHART_COLORS[2],
                    "values": [
                        warmup_comparison.get(topology_id, {}).get("warmup_freshness_p95_ms")
                        for topology_id in topology_ids
                    ],
                },
                {
                    "label": "Post Freshness P95",
                    "color": CHART_COLORS[1],
                    "values": [
                        warmup_comparison.get(topology_id, {}).get("post_warmup_freshness_p95_ms")
                        for topology_id in topology_ids
                    ],
                },
                {
                    "label": "Freshness Delta",
                    "color": CHART_COLORS[4],
                    "values": [
                        warmup_comparison.get(topology_id, {}).get("freshness_p95_delta_ms")
                        for topology_id in topology_ids
                    ],
                },
            ],
        },
    }

    sorted_connections = sorted(
        summary["connections"].items(),
        key=lambda item: (
            metric_from_distribution(item[1].get("freshness_ms", {}), "p95") or -1.0,
            metric_from_distribution(item[1].get("arrival_delta_ms", {}), "p95") or -1.0,
        ),
        reverse=True,
    )[:12]
    payload["connection_outliers"] = {
        "title": "Connection Outliers",
        "subtitle": "Worst 12 connections by freshness P95, with arrival P95 as a tie-breaker view.",
        "value_kind": "ms",
        "categories": [
            short_connection_label(connection_id) for connection_id, _ in sorted_connections
        ],
        "series": [
            {
                "label": "Freshness P95",
                "color": CHART_COLORS[2],
                "values": [
                    metric_from_distribution(row.get("freshness_ms", {}), "p95")
                    for _, row in sorted_connections
                ],
            },
            {
                "label": "Arrival P95",
                "color": CHART_COLORS[0],
                "values": [
                    metric_from_distribution(row.get("arrival_delta_ms", {}), "p95")
                    for _, row in sorted_connections
                ],
            },
        ],
        "connection_ids": [connection_id for connection_id, _ in sorted_connections],
    }

    if visualization_state is not None:
        run_started_ns = int(visualization_state["run_started_ns"])
        run_ended_ns = int(visualization_state["run_ended_ns"])
        run_span_ns = max(1, run_ended_ns - run_started_ns)
        rebind_markers = []
        for segment in summary.get("market_segments", [])[1:]:
            started_at_iso = segment.get("started_at_iso")
            if not started_at_iso:
                continue
            started_ns = iso_to_ns(started_at_iso)
            rebind_markers.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "market_slug": segment.get("market_slug"),
                    "offset_seconds": round(
                        (started_ns - run_started_ns) / 1_000_000_000,
                        3,
                    ),
                }
            )

        connection_order = list(visualization_state["connection_order"])
        payload["connection_event_timeline"] = {
            "title": "Per-Connection Event Timeline",
            "subtitle": "Each row is one websocket. Colored cells mean events arrived in that time bucket; white space means silence. Dashed lines mark market rebinds.",
            "duration_seconds": round(float(visualization_state["duration_seconds"]), 3),
            "bucket_count": int(visualization_state["bucket_count"]),
            "bucket_span_seconds": round(
                float(visualization_state["duration_seconds"]) / max(1, int(visualization_state["bucket_count"])),
                6,
            ),
            "intensity_ceiling": round(float(visualization_state["intensity_ceiling"]), 3),
            "phase_palette": {
                "warmup": "#f59e0b",
                "post_warmup_compare": "#10b981",
                "steady": "#2563eb",
            },
            "connection_rows": [
                {
                    "connection_id": connection_id,
                    "label": short_connection_label(connection_id),
                    "coverage_rate": summary["connections"][connection_id].get("coverage_rate", 0.0),
                }
                for connection_id in connection_order
            ],
            "total_counts": visualization_state["total_counts"],
            "phase_counts": visualization_state["phase_counts"],
            "rebind_markers": rebind_markers,
        }

        gap_intervals = build_topology_gap_intervals(
            aggregates=visualization_state["aggregates"],
            topology_ids=topology_ids,
        )
        payload["topology_gap_timeline"] = {
            "title": "Topology Gap Timeline",
            "subtitle": "Each bar marks a run where that topology missed events that other topologies did see.",
            "duration_seconds": round(float(visualization_state["duration_seconds"]), 3),
            "topology_rows": [
                {"topology_id": topology_id, "label": f"{topology_id} ws"}
                for topology_id in topology_ids
            ],
            "gap_intervals": {
                topology_id: [
                    {
                        "start_offset_seconds": round(
                            (interval["start_ns"] - run_started_ns) / 1_000_000_000,
                            3,
                        ),
                        "end_offset_seconds": round(
                            (interval["end_ns"] - run_started_ns) / 1_000_000_000,
                            3,
                        ),
                        "count": interval["count"],
                    }
                    for interval in intervals
                ]
                for topology_id, intervals in gap_intervals.items()
            },
            "rebind_markers": rebind_markers,
        }

    return payload


def write_visual_artifacts(
    summary: dict[str, Any],
    output_dir: Path,
    events_path: Optional[Path],
) -> dict[str, Any]:
    finalization_log("[finalize] generating visuals")
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    topology_ids = sorted(summary["topologies"], key=int)
    visualization_state: Optional[dict[str, Any]] = None
    if events_path is not None and events_path.exists():
        finalization_log("[finalize] loading event log for visuals")
        visualization_state = load_visualization_state(summary, events_path)
    visualization_data = build_visualization_payload(
        summary,
        visualization_state=visualization_state,
    )

    chart_assets: dict[str, dict[str, str]] = {}

    small_multiples_specs: dict[str, dict[str, Any]] = {
        "topology_performance": {
            "lower_is_better": [False, False, True],
        },
        "topology_latency": {
            "lower_is_better": [True, True, True, True],
        },
        "topology_gap_counts": {
            "lower_is_better": [True, True, True],
        },
        "topology_gap_durations": {
            "lower_is_better": [True, True, True],
        },
    }

    for chart_name in (
        "topology_performance",
        "topology_latency",
        "topology_gap_counts",
        "topology_gap_durations",
        "warmup_quality",
        "connection_outliers",
    ):
        chart_spec = visualization_data[chart_name]
        finalization_log(f"[finalize] rendering {chart_name}.svg")
        if chart_name in small_multiples_specs:
            lower_flags = small_multiples_specs[chart_name]["lower_is_better"]
            metrics = []
            for idx, row in enumerate(chart_spec["series"]):
                metrics.append(
                    {
                        "label": str(row["label"]),
                        "color": str(row["color"]),
                        "values": list(row["values"]),
                        "value_kind": str(chart_spec["value_kind"]),
                        "lower_is_better": bool(
                            lower_flags[idx] if idx < len(lower_flags) else False
                        ),
                    }
                )
            svg = render_small_multiples_svg(
                title=str(chart_spec["title"]),
                subtitle=str(chart_spec["subtitle"]),
                categories=chart_spec["categories"],
                metrics=metrics,
            )
        else:
            svg = render_grouped_bar_chart_svg(
                title=chart_spec["title"],
                subtitle=chart_spec["subtitle"],
                categories=chart_spec["categories"],
                series=[
                    ChartSeries(
                        str(row["label"]),
                        list(row["values"]),
                        str(row["color"]),
                    )
                    for row in chart_spec["series"]
                ],
                value_kind=str(chart_spec["value_kind"]),
            )
        relative_path = Path("charts") / f"{chart_name}.svg"
        (output_dir / relative_path).write_text(svg, encoding="utf-8")
        chart_assets[chart_name] = {
            "path": str(relative_path),
            "svg": svg,
            "title": str(chart_spec["title"]),
            "subtitle": str(chart_spec["subtitle"]),
        }

    if visualization_state is not None:
        finalization_log("[finalize] rendering connection_event_timeline.svg")
        event_timeline_svg = render_connection_event_timeline_svg(
            summary=summary,
            timeline_state=visualization_state,
        )
        event_timeline_chart_path = Path("charts") / "connection_event_timeline.svg"
        (output_dir / event_timeline_chart_path).write_text(event_timeline_svg, encoding="utf-8")
        chart_assets["connection_event_timeline"] = {
            "path": str(event_timeline_chart_path),
            "svg": event_timeline_svg,
            "title": str(visualization_data["connection_event_timeline"]["title"]),
            "subtitle": str(visualization_data["connection_event_timeline"]["subtitle"]),
        }

        finalization_log("[finalize] rendering topology_gap_timeline.svg")
        topology_gap_timeline_svg = render_topology_gap_timeline_svg(
            summary=summary,
            gap_intervals=build_topology_gap_intervals(
                aggregates=visualization_state["aggregates"],
                topology_ids=topology_ids,
            ),
        )
        topology_gap_timeline_chart_path = Path("charts") / "topology_gap_timeline.svg"
        (output_dir / topology_gap_timeline_chart_path).write_text(
            topology_gap_timeline_svg,
            encoding="utf-8",
        )
        chart_assets["topology_gap_timeline"] = {
            "path": str(topology_gap_timeline_chart_path),
            "svg": topology_gap_timeline_svg,
            "title": str(visualization_data["topology_gap_timeline"]["title"]),
            "subtitle": str(visualization_data["topology_gap_timeline"]["subtitle"]),
        }

    finalization_log("[finalize] writing report.html")
    report_path = output_dir / "report.html"
    report_path.write_text(render_report_html(summary, chart_assets), encoding="utf-8")
    return {
        "report_html": str(report_path),
        "charts": {
            name: str(output_dir / asset["path"])
            for name, asset in chart_assets.items()
        },
        "visualization_data": visualization_data,
    }


def write_json_line(handle: Any, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")


def verbose_log(config: BenchmarkConfig, message: str) -> None:
    if config.verbose:
        print(message, flush=True)


def finalization_log(message: str) -> None:
    print(message, flush=True)


def compare_window_seconds(config: BenchmarkConfig) -> float:
    if config.warmup_compare_window_seconds is None:
        return config.warmup_seconds
    return max(0.0, float(config.warmup_compare_window_seconds))


def market_start_at(config_row: dict[str, Any]) -> Optional[datetime]:
    return parse_market_datetime(
        config_row.get("start_time")
        or config_row.get("event_start_time")
        or config_row.get("event_start_date")
        or config_row.get("accepting_orders_timestamp")
    )


def market_end_at(config_row: dict[str, Any]) -> Optional[datetime]:
    return parse_market_datetime(
        config_row.get("end_time")
        or config_row.get("event_end_date")
        or config_row.get("closed_time")
        or config_row.get("uma_end_date")
    )


def market_is_openish(config_row: dict[str, Any]) -> bool:
    status = str(config_row.get("status") or "").upper()
    if status == "CLOSED":
        return False
    if config_row.get("event_closed") is True:
        return False
    return True


def extract_token_ids_from_config(config_row: dict[str, Any]) -> tuple[str, ...]:
    return dedupe_preserve_order(
        [
            str(config_row.get("up_token_id") or "").strip(),
            str(config_row.get("down_token_id") or "").strip(),
        ]
    )


def build_target_from_config(
    config_row: dict[str, Any],
    *,
    segment_index: int,
    switch_reason: str,
) -> BenchmarkTarget:
    market_slug = str(config_row.get("slug") or "").strip() or None
    token_ids = extract_token_ids_from_config(config_row)
    series_id = str(config_row.get("series_id") or "").strip() or None
    return BenchmarkTarget(
        market_slug=market_slug,
        token_ids=token_ids,
        series_id=series_id,
        segment_id=f"segment_{segment_index:03d}",
        switch_reason=switch_reason,
        start_at=market_start_at(config_row),
        end_at=market_end_at(config_row),
    )


def choose_series_market_config(
    configs: Sequence[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    current_market_slug: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    now = now or utc_now()
    candidates = [
        config_row
        for config_row in configs
        if str(config_row.get("slug") or "").strip() and market_is_openish(config_row)
    ]
    if not candidates:
        candidates = [
            config_row
            for config_row in configs
            if str(config_row.get("slug") or "").strip()
        ]
    if not candidates:
        return None, "no_candidates"

    active: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    for config_row in candidates:
        start_at = market_start_at(config_row)
        end_at = market_end_at(config_row)
        if end_at is not None and now >= end_at:
            continue
        if start_at is not None and now < start_at:
            upcoming.append(config_row)
            continue
        active.append(config_row)

    active.sort(
        key=lambda config_row: market_end_at(config_row)
        or datetime.max.replace(tzinfo=timezone.utc)
    )
    upcoming.sort(
        key=lambda config_row: (
            market_start_at(config_row) or datetime.max.replace(tzinfo=timezone.utc),
            market_end_at(config_row) or datetime.max.replace(tzinfo=timezone.utc),
        )
    )

    if current_market_slug:
        for config_row in upcoming:
            if str(config_row.get("slug") or "").strip() == current_market_slug:
                return config_row, "preserve_selected_preopen_window"

    if active:
        return active[0], "active_window"
    if upcoming:
        return upcoming[0], "no_active_use_next"

    fallback = sorted(
        candidates,
        key=lambda config_row: (
            market_start_at(config_row) or datetime.max.replace(tzinfo=timezone.utc),
            market_end_at(config_row) or datetime.max.replace(tzinfo=timezone.utc),
        ),
    )[0]
    return fallback, "fallback_window"


async def resolve_initial_target(
    config: BenchmarkConfig,
) -> tuple[BenchmarkTarget, Optional[str]]:
    if config.token_ids:
        status_log(
            f"[startup] using explicit token ids from config/CLI: {list(dedupe_preserve_order(config.token_ids))}"
        )
        target = BenchmarkTarget(
            market_slug=config.market_slug,
            token_ids=dedupe_preserve_order(config.token_ids),
            series_id=config.series_id,
            segment_id="segment_001",
            switch_reason="explicit_token_ids",
            start_at=None,
            end_at=None,
        )
        return target, config.series_id

    if config.market_slug:
        status_log(
            f"[startup] resolving market slug {config.market_slug!r} via Gamma API"
        )
        market_configs = await get_market_configurations(slug=config.market_slug)
        if not market_configs:
            raise ValueError(f"could not resolve market slug {config.market_slug!r}")
        config_row = market_configs[0]
        target = build_target_from_config(
            config_row,
            segment_index=1,
            switch_reason="explicit_market",
        )
        if not target.token_ids:
            raise ValueError(f"market {config.market_slug!r} did not include token ids")
        series_id = config.series_id or target.series_id
        if series_id is None:
            status_log(
                f"[startup] resolved market {target.market_slug!r} with token ids {list(target.token_ids)}"
            )
            return target, None
        target.series_id = series_id
        status_log(
            f"[startup] resolved market {target.market_slug!r} with token ids {list(target.token_ids)} (series={series_id})"
        )
        return target, series_id

    if not config.series_id:
        raise ValueError("provide --market, --series-id, or at least one --token-id")

    status_log(
        f"[startup] resolving active market for series {config.series_id!r} via Gamma API"
    )
    market_configs = await get_market_configurations(series_id=config.series_id)
    selected, selection_reason = choose_series_market_config(market_configs)
    if selected is None:
        raise ValueError(f"could not resolve an active/upcoming market for series {config.series_id!r}")
    target = build_target_from_config(
        selected,
        segment_index=1,
        switch_reason=selection_reason,
    )
    if not target.token_ids:
        raise ValueError(f"series {config.series_id!r} yielded a market without token ids")
    target.series_id = config.series_id
    status_log(
        "[startup] resolved "
        f"{target.market_slug!r} with token ids {list(target.token_ids)} "
        f"(series={config.series_id}, reason={selection_reason})"
    )
    return target, config.series_id


@dataclass(slots=True)
class TargetState:
    current_target: BenchmarkTarget
    version: int = 0
    segment_records: list[MarketSegmentRecord] = field(default_factory=list)

    def snapshot(self) -> tuple[BenchmarkTarget, int]:
        return self.current_target, self.version

    def update_target(self, target: BenchmarkTarget, *, now_ns: int) -> bool:
        current_slug = self.current_target.market_slug
        current_tokens = self.current_target.token_ids
        current_segment = self.current_target.segment_id
        if (
            current_slug == target.market_slug
            and current_tokens == target.token_ids
            and current_segment == target.segment_id
        ):
            if not self.segment_records:
                self.segment_records.append(
                    MarketSegmentRecord(
                        segment_id=target.segment_id,
                        market_slug=target.market_slug,
                        series_id=target.series_id,
                        token_ids=target.token_ids,
                        switch_reason=target.switch_reason,
                        started_at_ns=now_ns,
                        started_at_iso=ns_to_iso(now_ns),
                    )
                )
            return False

        if self.segment_records and self.segment_records[-1].ended_at_ns is None:
            self.segment_records[-1].close(now_ns)

        self.current_target = target
        self.version += 1
        self.segment_records.append(
            MarketSegmentRecord(
                segment_id=target.segment_id,
                market_slug=target.market_slug,
                series_id=target.series_id,
                token_ids=target.token_ids,
                switch_reason=target.switch_reason,
                started_at_ns=now_ns,
                started_at_iso=ns_to_iso(now_ns),
            )
        )
        return True

    def close_active_segment(self, ended_at_ns: int) -> None:
        if self.segment_records and self.segment_records[-1].ended_at_ns is None:
            self.segment_records[-1].close(ended_at_ns)


async def monitor_series_rebinding(
    *,
    config: BenchmarkConfig,
    series_id: str,
    target_state: TargetState,
    stop_event: asyncio.Event,
) -> None:
    segment_index = len(target_state.segment_records) + 1
    try:
        while not stop_event.is_set():
            await asyncio.sleep(config.series_refresh_seconds)
            market_configs = await get_market_configurations(series_id=series_id)
            selected, selection_reason = choose_series_market_config(
                market_configs,
                current_market_slug=target_state.current_target.market_slug,
            )
            if selected is None:
                continue
            candidate = build_target_from_config(
                selected,
                segment_index=segment_index,
                switch_reason=selection_reason,
            )
            candidate.series_id = series_id
            if not candidate.token_ids:
                continue
            if (
                candidate.market_slug == target_state.current_target.market_slug
                and candidate.token_ids == target_state.current_target.token_ids
            ):
                continue
            changed = target_state.update_target(candidate, now_ns=time.time_ns())
            if changed:
                segment_index += 1
                print()
                print(
                    f"[rebind] market -> {candidate.market_slug} "
                    f"(series={series_id}, reason={candidate.switch_reason})"
                )
    except asyncio.CancelledError:
        raise


async def maybe_send_control_response(
    websocket: Any,
    raw_message: str,
    connection_stats: ConnectionRuntimeStats,
) -> bool:
    if raw_message == "PING":
        connection_stats.control_messages += 1
        await websocket.send("PONG")
        return True
    if raw_message == "PONG":
        connection_stats.control_messages += 1
        return True
    return False


def iter_raw_events(decoded: Any) -> list[dict[str, Any]]:
    if isinstance(decoded, dict):
        return [decoded]
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]
    return []


async def send_market_subscription(websocket: Any, token_id: str) -> None:
    await websocket.send(
        json.dumps({"assets_ids": [token_id], "operation": "subscribe"})
    )


async def send_market_unsubscription(websocket: Any, token_id: str) -> None:
    await websocket.send(
        json.dumps({"assets_ids": [token_id], "operation": "unsubscribe"})
    )


async def handle_ws_frame(
    *,
    websocket: Any,
    raw_message: Any,
    received_at_ns: int,
    connection_stats: ConnectionRuntimeStats,
    target: BenchmarkTarget,
    event_types: set[str],
    queue: asyncio.Queue[Optional[Observation]],
    capture_raw_event: bool = False,
) -> None:
    if isinstance(raw_message, bytes):
        try:
            raw_text = raw_message.decode("utf-8")
        except UnicodeDecodeError:
            connection_stats.malformed_messages += 1
            return
    else:
        raw_text = str(raw_message)

    if not raw_text:
        connection_stats.malformed_messages += 1
        return

    if await maybe_send_control_response(websocket, raw_text, connection_stats):
        return

    try:
        decoded = _fast_loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        connection_stats.malformed_messages += 1
        return

    raw_events = iter_raw_events(decoded)
    if not raw_events:
        connection_stats.ignored_items += 1
        return
    if isinstance(decoded, list):
        ignored_count = len(decoded) - len(raw_events)
        if ignored_count > 0:
            connection_stats.ignored_items += ignored_count

    for raw_event in raw_events:
        event_type = normalize_event_type(raw_event.get("event_type") or raw_event.get("type"))
        if event_type == "ping":
            connection_stats.control_messages += 1
            await websocket.send(json.dumps({"type": "pong"}))
            continue
        if event_type in {"pong"}:
            connection_stats.control_messages += 1
            continue
        if not event_type or event_type == "invalid operation":
            connection_stats.filtered_messages += 1
            continue
        if event_type not in event_types:
            connection_stats.filtered_messages += 1
            continue

        asset_ids = extract_asset_ids(raw_event)
        asset_id = asset_ids[0] if len(asset_ids) == 1 else None
        parsed_ts = parse_venue_timestamp(raw_event.get("timestamp"))
        observation = Observation(
            event_key=build_event_key(
                event_type,
                raw_event,
                scope=target.segment_id,
            ),
            event_type=event_type,
            asset_id=asset_id,
            asset_ids=asset_ids,
            market_slug=target.market_slug,
            series_id=target.series_id,
            segment_id=target.segment_id,
            switch_reason=target.switch_reason,
            phase_kind=connection_stats.phase_kind(received_at_ns),
            connection_id=connection_stats.connection_id,
            topology_id=connection_stats.topology_id,
            topology_size=connection_stats.topology_size,
            received_at_ns=received_at_ns,
            received_at_iso=ns_to_iso(received_at_ns),
            in_warmup=connection_stats.in_warmup(received_at_ns),
            venue_timestamp_raw=parsed_ts.raw,
            venue_timestamp_ns=parsed_ts.epoch_ns,
            venue_timestamp_iso=parsed_ts.iso,
            venue_timestamp_parse_mode=parsed_ts.parse_mode,
            raw_event=raw_event if capture_raw_event else None,
        )
        connection_stats.total_events += 1
        await queue.put(observation)


async def run_connection_worker(
    *,
    config: BenchmarkConfig,
    target_state: TargetState,
    connection_stats: ConnectionRuntimeStats,
    queue: asyncio.Queue[Optional[Observation]],
    stop_event: asyncio.Event,
    capture_raw_event: bool = False,
) -> None:
    tracked_event_types = set(config.event_types)
    compare_seconds = compare_window_seconds(config)

    async def apply_target(
        websocket: Any,
        *,
        target: BenchmarkTarget,
        subscribed_token_ids: set[str],
    ) -> set[str]:
        next_tokens = set(target.token_ids)
        removed = sorted(subscribed_token_ids - next_tokens)
        added = sorted(next_tokens - subscribed_token_ids)
        for token_id in removed:
            await send_market_unsubscription(websocket, token_id)
        for token_id in added:
            await send_market_subscription(websocket, token_id)
        connection_stats.note_market_target(
            now_ns=time.time_ns(),
            market_slug=target.market_slug,
            series_id=target.series_id,
            segment_id=target.segment_id,
            switch_reason=target.switch_reason,
            warmup_seconds=config.warmup_seconds,
            compare_window_seconds=compare_seconds,
        )
        return next_tokens

    while not stop_event.is_set():
        try:
            async with websockets.connect(
                config.ws_url,
                open_timeout=config.open_timeout_seconds,
                ping_interval=config.ping_interval_seconds,
                ping_timeout=config.ping_interval_seconds,
                max_queue=4,
            ) as websocket:
                now_monotonic = time.monotonic()
                connection_stats.note_connect(
                    now_monotonic,
                    time.time_ns(),
                    config.warmup_seconds,
                )
                verbose_log(
                    config,
                    f"[connect] {connection_stats.connection_id} -> {config.ws_url}",
                )
                target, version = target_state.snapshot()
                subscribed_token_ids = await apply_target(
                    websocket,
                    target=target,
                    subscribed_token_ids=set(),
                )

                while not stop_event.is_set():
                    latest_target, latest_version = target_state.snapshot()
                    if latest_version != version:
                        subscribed_token_ids = await apply_target(
                            websocket,
                            target=latest_target,
                            subscribed_token_ids=subscribed_token_ids,
                        )
                        target = latest_target
                        version = latest_version
                    try:
                        raw_message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    received_at_ns = time.time_ns()

                    connection_stats.total_messages += 1
                    connection_stats.note_message(time.monotonic())
                    await handle_ws_frame(
                        websocket=websocket,
                        raw_message=raw_message,
                        received_at_ns=received_at_ns,
                        connection_stats=connection_stats,
                        target=target,
                        event_types=tracked_event_types,
                        queue=queue,
                        capture_raw_event=capture_raw_event,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if connection_stats.successful_connects == 0 and not connection_stats.connected:
                connection_stats.note_connect_failure(exc)
            else:
                connection_stats.note_disconnect(exc)
            verbose_log(
                config,
                f"[disconnect] {connection_stats.connection_id} -> {exc}",
            )
            if stop_event.is_set():
                break
            await asyncio.sleep(config.reconnect_delay_seconds)
        else:
            if not stop_event.is_set():
                connection_stats.note_disconnect(RuntimeError("connection closed"))
                await asyncio.sleep(config.reconnect_delay_seconds)


async def consume_observations(
    *,
    queue: asyncio.Queue[Optional[Observation]],
    aggregators: Sequence[MetricsAggregator],
    events_handle: Optional[Any],
    include_raw_event_payload: bool,
) -> None:
    while True:
        observation = await queue.get()
        if observation is None:
            break
        for aggregator in aggregators:
            aggregator.record_observation(observation)
        if events_handle is not None:
            write_json_line(
                events_handle,
                observation.to_record(include_raw_event=include_raw_event_payload),
            )


def _resolve_malloc_trim() -> Optional[Callable[[int], int]]:
    # Glibc-only: returns freed arena pages to the OS, mitigating
    # fragmentation from high-churn json.loads workloads. Silent no-op
    # elsewhere (macOS, musl, etc.).
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        return None
    try:
        libc = ctypes.CDLL(libc_name)
    except OSError:
        return None
    trim = getattr(libc, "malloc_trim", None)
    if trim is None:
        return None
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    return trim


async def run_memory_hygiene(*, stop_event: asyncio.Event, interval_seconds: float = 30.0) -> None:
    trim = _resolve_malloc_trim()
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
            gc.collect()
            if trim is not None:
                trim(0)
    except asyncio.CancelledError:
        pass


def print_progress(
    *,
    elapsed_seconds: float,
    snapshot: dict[str, Any],
    connection_stats: dict[str, ConnectionRuntimeStats],
) -> None:
    print(flush=True)
    print(
        f"[{format_elapsed(elapsed_seconds)}] "
        f"scored union events={snapshot['union_event_count']} "
        f"rss={format_bytes(current_rss_bytes())}",
        flush=True,
    )
    for topology_id, row in sorted(snapshot["topologies"].items(), key=lambda item: int(item[0])):
        print(
            "  "
            f"topology {topology_id}: "
            f"seen={row['seen_events']} "
            f"coverage={row['coverage_rate']:.3f} "
            f"first_seen={row['first_seen_win_rate']:.3f} "
            f"relative_miss={row['relative_miss_count']}",
            flush=True,
        )

    now_monotonic = time.monotonic()
    for connection_id, runtime in sorted(connection_stats.items()):
        connection_snapshot = runtime.snapshot(now_monotonic)
        print(
            "    "
            f"{connection_id}: "
            f"connected={'yes' if connection_snapshot['connected'] else 'no'} "
            f"msgs={connection_snapshot['total_messages']} "
            f"disc={connection_snapshot['disconnects']} "
            f"reco={connection_snapshot['reconnects']} "
            f"silent={connection_snapshot['current_silence_seconds']}",
            flush=True,
        )


def record_connection_snapshots(
    *,
    handle: Optional[Any],
    phase: str,
    elapsed_seconds: float,
    connection_stats: dict[str, ConnectionRuntimeStats],
) -> None:
    if handle is None:
        return
    now_iso = utc_now().isoformat()
    now_monotonic = time.monotonic()
    for connection_id, runtime in sorted(connection_stats.items()):
        write_json_line(
            handle,
            {
                "phase": phase,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "recorded_at": now_iso,
                **runtime.snapshot(now_monotonic),
                "connection_id": connection_id,
            },
        )


async def report_progress(
    *,
    config: BenchmarkConfig,
    aggregator: MetricsAggregator,
    connection_stats: dict[str, ConnectionRuntimeStats],
    run_started_monotonic: float,
    stop_event: asyncio.Event,
    connections_handle: Optional[Any],
) -> None:
    try:
        while not stop_event.is_set():
            await asyncio.sleep(config.progress_interval_seconds)
            elapsed_seconds = time.monotonic() - run_started_monotonic
            snapshot = aggregator.snapshot()
            print_progress(
                elapsed_seconds=elapsed_seconds,
                snapshot=snapshot,
                connection_stats=connection_stats,
            )
            record_connection_snapshots(
                handle=connections_handle,
                phase="progress",
                elapsed_seconds=elapsed_seconds,
                connection_stats=connection_stats,
            )
    except asyncio.CancelledError:
        pass


async def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    status_log("[startup] initializing Polymarket CLOB websocket benchmark")
    initial_target, series_id = await resolve_initial_target(config)

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    events_path = output_dir / "events.jsonl"
    connections_path = output_dir / "connections.jsonl"
    events_path_for_visuals = events_path if config.write_event_log else None

    status_log(f"Benchmarking Polymarket CLOB WS: market={initial_target.market_slug!r}")
    if series_id:
        status_log(f"Series ID: {series_id}")
    status_log(f"Token IDs: {list(initial_target.token_ids)}")
    status_log(f"Topologies: {list(config.topologies)}")
    status_log(f"Duration: {config.duration_seconds:.1f}s")
    status_log(f"Warmup: {config.warmup_seconds:.1f}s")
    status_log(f"Warmup Compare Window: {compare_window_seconds(config):.1f}s")
    status_log(f"Output: {output_dir}")

    run_started_at = utc_now().isoformat()
    run_started_ns = time.time_ns()
    run_started_monotonic = time.monotonic()

    # Queue size bounds peak RAM: each Observation is ~1 KB without raw_event,
    # far larger with it. 10_000 keeps worst-case backpressure under ~100 MB
    # even on a 1 GB host; the consumer usually drains well below this.
    queue: asyncio.Queue[Optional[Observation]] = asyncio.Queue(maxsize=10_000)
    capture_raw_event = config.write_event_log and config.include_raw_event_payload
    stop_event = asyncio.Event()
    target_state = TargetState(current_target=initial_target)
    target_state.update_target(initial_target, now_ns=run_started_ns)

    connection_stats: dict[str, ConnectionRuntimeStats] = {}
    connection_ids_by_topology: dict[str, list[str]] = {}
    worker_tasks: list[asyncio.Task[Any]] = []
    worker_count = sum(config.topologies)
    status_log(
        f"[startup] launching {worker_count} websocket workers across {len(config.topologies)} topologies"
    )

    for topology_size in config.topologies:
        topology_id = str(topology_size)
        topology_connection_ids: list[str] = []
        for idx in range(topology_size):
            connection_id = f"topology_{topology_size}_conn_{idx + 1:02d}"
            topology_connection_ids.append(connection_id)
            runtime = ConnectionRuntimeStats(
                connection_id=connection_id,
                topology_id=topology_id,
                topology_size=topology_size,
            )
            connection_stats[connection_id] = runtime
            worker_tasks.append(
                asyncio.create_task(
                    run_connection_worker(
                        config=config,
                        target_state=target_state,
                        connection_stats=runtime,
                        queue=queue,
                        stop_event=stop_event,
                        capture_raw_event=capture_raw_event,
                    )
                )
            )
        connection_ids_by_topology[topology_id] = topology_connection_ids

    aggregator = MetricsAggregator(
        topology_ids=[str(size) for size in config.topologies],
        connection_ids_by_topology=connection_ids_by_topology,
        event_retention_seconds=config.event_retention_seconds,
    )
    warmup_aggregator = MetricsAggregator(
        topology_ids=[str(size) for size in config.topologies],
        connection_ids_by_topology=connection_ids_by_topology,
        scoring_filter=lambda observation: observation.phase_kind == "warmup",
        event_retention_seconds=config.event_retention_seconds,
    )
    post_warmup_aggregator = MetricsAggregator(
        topology_ids=[str(size) for size in config.topologies],
        connection_ids_by_topology=connection_ids_by_topology,
        scoring_filter=lambda observation: observation.phase_kind == "post_warmup_compare",
        event_retention_seconds=config.event_retention_seconds,
    )

    with (
        (
            events_path.open("w", encoding="utf-8", buffering=1)
            if config.write_event_log
            else contextlib.nullcontext(None)
        ) as events_handle,
        (
            connections_path.open("w", encoding="utf-8", buffering=1)
            if config.write_connection_log
            else contextlib.nullcontext(None)
        ) as connections_handle,
    ):
        consumer_task = asyncio.create_task(
            consume_observations(
                queue=queue,
                aggregators=(aggregator, warmup_aggregator, post_warmup_aggregator),
                events_handle=events_handle,
                include_raw_event_payload=config.include_raw_event_payload,
            )
        )
        reporter_task = asyncio.create_task(
            report_progress(
                config=config,
                aggregator=aggregator,
                connection_stats=connection_stats,
                run_started_monotonic=run_started_monotonic,
                stop_event=stop_event,
                connections_handle=connections_handle,
            )
        )
        hygiene_task = asyncio.create_task(
            run_memory_hygiene(stop_event=stop_event)
        )
        status_log(
            f"[startup] progress reporter active; first snapshot in {config.progress_interval_seconds:.1f}s"
        )
        series_task: Optional[asyncio.Task[Any]] = None
        if series_id:
            status_log(
                f"[startup] automatic series rebinding enabled; refresh interval={config.series_refresh_seconds:.1f}s"
            )
            series_task = asyncio.create_task(
                monitor_series_rebinding(
                    config=config,
                    series_id=series_id,
                    target_state=target_state,
                    stop_event=stop_event,
                )
            )

        try:
            await asyncio.sleep(config.duration_seconds)
        finally:
            stop_event.set()
            if series_task is not None:
                series_task.cancel()
            finalization_log("[finalize] stopping websocket workers")
            worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)
            series_results: list[Any] = []
            if series_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await series_task
            finalization_log("[finalize] draining remaining observations")
            await queue.put(None)
            await consumer_task
            reporter_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reporter_task
            hygiene_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hygiene_task
            record_connection_snapshots(
                handle=connections_handle,
                phase="final",
                elapsed_seconds=time.monotonic() - run_started_monotonic,
                connection_stats=connection_stats,
            )

    run_ended_at = utc_now().isoformat()
    run_ended_ns = time.time_ns()
    target_state.close_active_segment(run_ended_ns)
    finalization_log("[finalize] building summaries")
    summary = aggregator.build_summary(
        config=BenchmarkConfig(
            market_slug=initial_target.market_slug,
            token_ids=initial_target.token_ids,
            series_id=series_id,
            duration_seconds=config.duration_seconds,
            topologies=config.topologies,
            warmup_seconds=config.warmup_seconds,
            warmup_compare_window_seconds=config.warmup_compare_window_seconds,
            ping_interval_seconds=config.ping_interval_seconds,
            progress_interval_seconds=config.progress_interval_seconds,
            series_refresh_seconds=config.series_refresh_seconds,
            event_types=config.event_types,
            output_dir=output_dir,
            ws_url=config.ws_url,
            reconnect_delay_seconds=config.reconnect_delay_seconds,
            open_timeout_seconds=config.open_timeout_seconds,
            event_retention_seconds=config.event_retention_seconds,
            generate_visuals=config.generate_visuals,
            write_event_log=config.write_event_log,
            write_connection_log=config.write_connection_log,
            include_raw_event_payload=config.include_raw_event_payload,
            verbose=config.verbose,
        ),
        token_ids=initial_target.token_ids,
        run_started_at=run_started_at,
        run_started_ns=run_started_ns,
        run_ended_at=run_ended_at,
        run_ended_ns=run_ended_ns,
        output_dir=output_dir,
        connection_stats=connection_stats,
    )

    finalization_log("[finalize] building warmup summaries")
    warmup_summary = warmup_aggregator.build_summary(
        config=BenchmarkConfig(
            market_slug=initial_target.market_slug,
            token_ids=initial_target.token_ids,
            series_id=series_id,
            duration_seconds=config.duration_seconds,
            topologies=config.topologies,
            warmup_seconds=config.warmup_seconds,
            warmup_compare_window_seconds=config.warmup_compare_window_seconds,
            ping_interval_seconds=config.ping_interval_seconds,
            progress_interval_seconds=config.progress_interval_seconds,
            series_refresh_seconds=config.series_refresh_seconds,
            event_types=config.event_types,
            output_dir=output_dir,
            ws_url=config.ws_url,
            reconnect_delay_seconds=config.reconnect_delay_seconds,
            open_timeout_seconds=config.open_timeout_seconds,
            event_retention_seconds=config.event_retention_seconds,
            generate_visuals=config.generate_visuals,
            write_event_log=config.write_event_log,
            write_connection_log=config.write_connection_log,
            include_raw_event_payload=config.include_raw_event_payload,
            verbose=config.verbose,
        ),
        token_ids=initial_target.token_ids,
        run_started_at=run_started_at,
        run_started_ns=run_started_ns,
        run_ended_at=run_ended_at,
        run_ended_ns=run_ended_ns,
        output_dir=output_dir,
        connection_stats=connection_stats,
    )
    post_warmup_summary = post_warmup_aggregator.build_summary(
        config=BenchmarkConfig(
            market_slug=initial_target.market_slug,
            token_ids=initial_target.token_ids,
            series_id=series_id,
            duration_seconds=config.duration_seconds,
            topologies=config.topologies,
            warmup_seconds=config.warmup_seconds,
            warmup_compare_window_seconds=config.warmup_compare_window_seconds,
            ping_interval_seconds=config.ping_interval_seconds,
            progress_interval_seconds=config.progress_interval_seconds,
            series_refresh_seconds=config.series_refresh_seconds,
            event_types=config.event_types,
            output_dir=output_dir,
            ws_url=config.ws_url,
            reconnect_delay_seconds=config.reconnect_delay_seconds,
            open_timeout_seconds=config.open_timeout_seconds,
            event_retention_seconds=config.event_retention_seconds,
            generate_visuals=config.generate_visuals,
            write_event_log=config.write_event_log,
            write_connection_log=config.write_connection_log,
            include_raw_event_payload=config.include_raw_event_payload,
            verbose=config.verbose,
        ),
        token_ids=initial_target.token_ids,
        run_started_at=run_started_at,
        run_started_ns=run_started_ns,
        run_ended_at=run_ended_at,
        run_ended_ns=run_ended_ns,
        output_dir=output_dir,
        connection_stats=connection_stats,
    )

    summary["run_metadata"]["series_id"] = series_id
    summary["run_metadata"]["series_refresh_seconds"] = config.series_refresh_seconds
    summary["run_metadata"]["warmup_compare_window_seconds"] = compare_window_seconds(
        config
    )
    summary["run_metadata"]["market_rotation_enabled"] = bool(series_id)
    summary["run_metadata"]["initial_segment"] = initial_target.to_dict()
    summary["run_metadata"]["observed_market_slugs"] = [
        record.market_slug
        for record in target_state.segment_records
        if record.market_slug
    ]
    summary["market_segments"] = [record.to_dict() for record in target_state.segment_records]
    summary["warmup_evidence"] = build_warmup_evidence(
        warmup_summary=warmup_summary,
        post_warmup_summary=post_warmup_summary,
        compare_window_seconds=compare_window_seconds(config),
    )

    runtime_errors = [
        str(result)
        for result in worker_results
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
    ]
    if runtime_errors:
        summary["runtime_errors"] = runtime_errors

    if config.generate_visuals:
        try:
            visual_artifacts = write_visual_artifacts(
                summary,
                output_dir,
                events_path_for_visuals,
            )
            summary["visual_artifacts"] = {
                key: value
                for key, value in visual_artifacts.items()
                if key != "visualization_data"
            }
            summary["visualization_data"] = visual_artifacts["visualization_data"]
        except Exception as exc:
            summary.setdefault("runtime_errors", []).append(
                f"visualization generation failed: {exc}"
            )
            summary["visualization_data"] = build_visualization_payload(summary)
    else:
        summary["visualization_data"] = build_visualization_payload(summary)

    finalization_log("[finalize] writing summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(flush=True)
    status_log(f"Summary written to {summary_path}")
    if config.generate_visuals and "visual_artifacts" in summary:
        status_log(f"HTML report written to {summary['visual_artifacts']['report_html']}")
    return summary


def build_default_output_dir() -> Path:
    stamp = utc_now().strftime("%Y%m%d_%H%M%S")
    return Path("recordings/ws-bench") / stamp


def build_parser(defaults: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark Polymarket CLOB websocket topologies."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=defaults.get("config"),
        help=(
            "Optional path to a JSON or TOML config file. "
            f"Defaults to {DEFAULT_CONFIG_FILENAME} next to this script when present. "
            "CLI flags override values from the file."
        ),
    )
    parser.add_argument(
        "--market",
        default=defaults["market"],
        help="Polymarket market slug to resolve via Gamma API.",
    )
    parser.add_argument(
        "--market-id",
        dest="market",
        default=defaults["market"],
        help="Alias for --market.",
    )
    parser.add_argument(
        "--series-id",
        "--event-series-id",
        dest="series_id",
        default=defaults["series_id"],
        help="Polymarket event series id for automatic market rebinding.",
    )
    parser.add_argument(
        "--token-id",
        action="append",
        dest="token_ids",
        default=list(defaults["token_ids"]),
        help="Direct token id override. Repeat to benchmark multiple tokens.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=defaults["duration"],
        help="Benchmark duration in seconds (default: 300).",
    )
    parser.add_argument(
        "--topologies",
        default=defaults["topologies"],
        help="Comma-separated topology sizes to compare (default: 1,2,5,10).",
    )
    parser.add_argument(
        "--warmup-seconds",
        type=float,
        default=defaults["warmup_seconds"],
        help="Warmup window excluded from scored metrics (default: 10).",
    )
    parser.add_argument(
        "--warmup-compare-window-seconds",
        type=float,
        default=defaults["warmup_compare_window_seconds"],
        help="Immediate post-warmup comparison window in seconds. Defaults to --warmup-seconds.",
    )
    parser.add_argument(
        "--ping-interval-seconds",
        type=float,
        default=defaults["ping_interval_seconds"],
        help="WebSocket ping interval in seconds (default: 20).",
    )
    parser.add_argument(
        "--event-retention-seconds",
        type=float,
        default=defaults["event_retention_seconds"],
        help="How long to retain unique event keys in memory for cross-socket dedupe before finalizing summary metrics (default: 30).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=defaults["output_dir"],
        help="Output directory. Defaults to recordings/ws-bench/<timestamp> in this repo.",
    )
    parser.add_argument(
        "--event-types",
        default=defaults["event_types"],
        help="Comma-separated event types to track (default: book,price_change,last_trade_price).",
    )
    parser.add_argument(
        "--progress-interval-seconds",
        type=float,
        default=defaults["progress_interval_seconds"],
        help="Progress reporting interval in seconds (default: 5).",
    )
    parser.add_argument(
        "--series-refresh-seconds",
        type=float,
        default=defaults["series_refresh_seconds"],
        help="How often to refresh series market selection for automatic rebinding (default: 5).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=defaults["verbose"],
        help="Print connection-level lifecycle messages.",
    )
    parser.add_argument(
        "--write-visuals",
        action="store_true",
        default=defaults["write_visuals"],
        help="Generate report.html and SVG charts. By default the benchmark is summary-only and embeds chart-ready data in summary.json.",
    )
    parser.add_argument(
        "--skip-visuals",
        action="store_true",
        default=defaults["skip_visuals"],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--write-event-log",
        action="store_true",
        default=defaults["write_event_log"],
        help="Write events.jsonl for deep debugging. By default runs keep only summary aggregates.",
    )
    parser.add_argument(
        "--skip-event-log",
        action="store_true",
        default=defaults["skip_event_log"],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--write-connection-log",
        action="store_true",
        default=defaults["write_connection_log"],
        help="Write connections.jsonl progress snapshots. By default runs keep only summary aggregates.",
    )
    parser.add_argument(
        "--skip-connection-log",
        action="store_true",
        default=defaults["skip_connection_log"],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--include-raw-event-payload",
        action="store_true",
        default=defaults["include_raw_event_payload"],
        help="Include the full raw websocket payload in events.jsonl. This can make logs very large.",
    )
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    prelim_args, _ = pre_parser.parse_known_args(argv_list)

    defaults = _default_cli_config()
    config_path = prelim_args.config
    if config_path is None:
        default_config_path = _default_config_path()
        if default_config_path.is_file():
            config_path = default_config_path

    if config_path is not None:
        try:
            loaded = load_config_file(config_path)
        except FileNotFoundError:
            raise SystemExit(f"config file not found: {config_path}")
        except Exception as exc:
            raise SystemExit(f"failed to load config file {config_path}: {exc}")
        defaults.update(loaded)
        defaults["config"] = config_path

    parser = build_parser(defaults)
    args = parser.parse_args(argv_list)
    if config_path is not None:
        status_log(f"[config] loaded settings from {config_path}")

    if not args.market and not args.token_ids and not args.series_id:
        parser.error("provide --market, --series-id, or at least one --token-id")

    try:
        args.topologies = parse_csv_topologies(args.topologies)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        args.event_types = parse_csv_event_types(args.event_types)
    except ValueError as exc:
        parser.error(str(exc))

    if args.duration <= 0:
        parser.error("--duration must be > 0")
    if args.warmup_seconds < 0:
        parser.error("--warmup-seconds must be >= 0")
    if args.warmup_compare_window_seconds is not None and args.warmup_compare_window_seconds < 0:
        parser.error("--warmup-compare-window-seconds must be >= 0")
    if args.ping_interval_seconds <= 0:
        parser.error("--ping-interval-seconds must be > 0")
    if args.progress_interval_seconds <= 0:
        parser.error("--progress-interval-seconds must be > 0")
    if args.series_refresh_seconds <= 0:
        parser.error("--series-refresh-seconds must be > 0")
    if args.event_retention_seconds <= 0:
        parser.error("--event-retention-seconds must be > 0")
    if args.write_visuals and args.skip_visuals:
        parser.error("choose either --write-visuals or --skip-visuals, not both")
    if args.write_event_log and args.skip_event_log:
        parser.error("choose either --write-event-log or --skip-event-log, not both")
    if args.write_connection_log and args.skip_connection_log:
        parser.error("choose either --write-connection-log or --skip-connection-log, not both")
    if args.include_raw_event_payload and not args.write_event_log:
        parser.error("--include-raw-event-payload requires --write-event-log")

    args.output_dir = args.output_dir or build_default_output_dir()
    return args


async def async_main(argv: Optional[Sequence[str]] = None) -> int:
    loop = asyncio.get_running_loop()
    status_log(
        f"[perf] loop={type(loop).__module__} "
        f"orjson={'yes' if _HAS_ORJSON else 'no'} "
        f"uvloop={'yes' if _HAS_UVLOOP else 'no'}"
    )

    if os.environ.get("BENCHMARK_ASYNCIO_DEBUG"):
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        try:
            threshold = float(os.environ.get("BENCHMARK_SLOW_CALLBACK_MS", "100")) / 1000.0
        except ValueError:
            threshold = 0.1
        loop.slow_callback_duration = threshold
        status_log(
            f"[debug] asyncio debug on; slow_callback_duration={threshold:.3f}s "
            f"(warnings go to stderr)"
        )

    args = parse_args(argv)
    config = BenchmarkConfig(
        market_slug=args.market,
        token_ids=dedupe_preserve_order(args.token_ids),
        series_id=args.series_id,
        duration_seconds=args.duration,
        topologies=args.topologies,
        warmup_seconds=args.warmup_seconds,
        warmup_compare_window_seconds=args.warmup_compare_window_seconds,
        ping_interval_seconds=args.ping_interval_seconds,
        progress_interval_seconds=args.progress_interval_seconds,
        series_refresh_seconds=args.series_refresh_seconds,
        event_types=args.event_types,
        output_dir=args.output_dir,
        event_retention_seconds=args.event_retention_seconds,
        generate_visuals=args.write_visuals and not args.skip_visuals,
        write_event_log=args.write_event_log and not args.skip_event_log,
        write_connection_log=args.write_connection_log and not args.skip_connection_log,
        include_raw_event_payload=args.include_raw_event_payload,
        verbose=args.verbose,
    )
    await run_benchmark(config)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    if _HAS_UVLOOP:
        uvloop.install()
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
