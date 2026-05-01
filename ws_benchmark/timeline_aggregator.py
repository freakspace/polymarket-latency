#!/usr/bin/env python3
"""Per-socket timeline aggregator.

Streams `<recording>/events.jsonl` and `<recording>/connections.jsonl` and
writes precomputed artifacts under `<recording>/timeline/` that the Next.js
dashboard can render as a per-connection swimlane:

    timeline/
        index.json                   # manifest + sparse byte-offset index
        buckets_60s.json             # full-run overview (one row per (conn, 60s))
        conn/<connection_id>.10s.json  # medium-zoom shard, fetched on brush-zoom
        transitions.json             # discrete state changes (reconnect, etc.)
        gaps.json                    # continuous silences > GAP_THRESHOLD_SECONDS

Pure stdlib so it can run in any venv. Single streaming pass over events.jsonl
plus a tiny linear scan of connections.jsonl.

Usage:
    python ws_benchmark/timeline_aggregator.py <recording-dir-or-summary.json>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from bisect import insort
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Sparse index entry every N events.jsonl lines. 5000 keeps the index ~300 KB
# for a full 48h run and gives ~25 ms of seek-then-scan worst-case at 10 msg/s.
DEFAULT_INDEX_STRIDE = 5000

# Continuous silences longer than this are emitted as discrete gap events that
# the dashboard renders as red blocks across the affected row.
GAP_THRESHOLD_SECONDS = 2.0

# Per-bucket reservoir cap. A 60s bucket at 50 msg/s holds 3000 deltas; we cap
# to keep memory bounded if a socket bursts. P50/P95 from a sorted reservoir is
# fine for the visualization (this is not the source of truth — summary.json is).
BUCKET_SAMPLE_CAP = 2048

BUCKET_60S_NS = 60 * 1_000_000_000
BUCKET_10S_NS = 10 * 1_000_000_000


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


@dataclass(slots=True)
class _BucketAccum:
    msg_count: int = 0
    arrival_deltas_ms: list[float] = field(default_factory=list)
    freshnesses_ms: list[float] = field(default_factory=list)
    in_warmup: bool = False  # any event in this bucket flagged in_warmup

    def add(
        self,
        *,
        arrival_delta_ms: float,
        freshness_ms: float | None,
        in_warmup: bool,
    ) -> None:
        self.msg_count += 1
        if len(self.arrival_deltas_ms) < BUCKET_SAMPLE_CAP:
            self.arrival_deltas_ms.append(arrival_delta_ms)
        if freshness_ms is not None and len(self.freshnesses_ms) < BUCKET_SAMPLE_CAP:
            self.freshnesses_ms.append(freshness_ms)
        if in_warmup:
            self.in_warmup = True

    def to_row(self, bucket_idx: int) -> list[Any]:
        ad = sorted(self.arrival_deltas_ms)
        fr = sorted(self.freshnesses_ms)
        return [
            bucket_idx,
            self.msg_count,
            _round_or_none(_percentile(ad, 0.50)),
            _round_or_none(_percentile(ad, 0.95)),
            _round_or_none(_percentile(fr, 0.95)),
            1 if self.in_warmup else 0,
        ]


def _round_or_none(v: float | None) -> float | None:
    if v is None:
        return None
    return round(v, 3)


@dataclass
class _PerTopologyFirstSeen:
    """Tracks the first received_at_ns per event_key, per topology, with TTL pruning."""

    retention_ns: int
    first_seen: dict[str, int] = field(default_factory=dict)
    queue: deque[tuple[int, str]] = field(default_factory=deque)  # (received_at_ns, key)

    def observe(self, *, event_key: str, received_at_ns: int) -> int:
        # Drop entries older than retention relative to current event time. We
        # use the *current* event's ts as the clock since events.jsonl is
        # ordered roughly chronologically.
        cutoff = received_at_ns - self.retention_ns
        q = self.queue
        while q and q[0][0] < cutoff:
            old_ns, old_key = q.popleft()
            cur = self.first_seen.get(old_key)
            if cur is not None and cur == old_ns:
                del self.first_seen[old_key]
        first = self.first_seen.get(event_key)
        if first is None or received_at_ns < first:
            self.first_seen[event_key] = received_at_ns
            self.queue.append((received_at_ns, event_key))
            return received_at_ns
        return first


def _aggregate_events(
    events_path: Path,
    *,
    retention_seconds: float,
    index_stride: int,
) -> dict[str, Any]:
    """Single streaming pass: builds buckets, gaps, transitions metadata, and
    a sparse byte-offset index into events.jsonl.

    A pre-scan determines a stable origin (the earliest received_at_ns), so
    bucket indices are non-negative even if the first connection to log isn't
    the one with the earliest event."""
    retention_ns = int(retention_seconds * 1_000_000_000)
    first_seen_by_topo: dict[str, _PerTopologyFirstSeen] = defaultdict(
        lambda: _PerTopologyFirstSeen(retention_ns=retention_ns)
    )

    buckets_60s: dict[tuple[str, int], _BucketAccum] = {}
    buckets_10s: dict[tuple[str, int], _BucketAccum] = {}

    conn_meta: dict[str, dict[str, Any]] = {}
    last_msg_ns: dict[str, int] = {}
    gaps: list[dict[str, Any]] = []
    byte_index: list[list[int]] = []

    run_started_ns: int | None = None
    run_ended_ns: int | None = None
    line_count = 0
    skipped_lines = 0

    # First we need a stable origin for bucket indices. We could scan-twice,
    # but we instead lazily rebase: keep raw absolute (received_at_ns) buckets
    # via integer division by width, then translate at materialize time.
    with events_path.open("rb") as fh:
        offset = fh.tell()
        for raw in fh:
            try:
                rec = json.loads(raw)
            except Exception:
                skipped_lines += 1
                offset = fh.tell()
                continue

            received_at_ns = rec.get("received_at_ns")
            connection_id = rec.get("connection_id")
            topology_id = rec.get("topology_id")
            event_key = rec.get("event_key")
            if (
                not isinstance(received_at_ns, int)
                or not connection_id
                or not topology_id
                or not event_key
            ):
                skipped_lines += 1
                offset = fh.tell()
                continue

            if line_count % index_stride == 0:
                byte_index.append([offset, received_at_ns])
            line_count += 1

            if run_started_ns is None or received_at_ns < run_started_ns:
                run_started_ns = received_at_ns
            if run_ended_ns is None or received_at_ns > run_ended_ns:
                run_ended_ns = received_at_ns

            if connection_id not in conn_meta:
                conn_meta[connection_id] = {
                    "connection_id": connection_id,
                    "topology_id": str(topology_id),
                    "topology_size": rec.get("topology_size"),
                }

            prev = last_msg_ns.get(connection_id)
            if prev is not None:
                gap_ns = received_at_ns - prev
                if gap_ns >= GAP_THRESHOLD_SECONDS * 1_000_000_000:
                    gaps.append(
                        {
                            "connection_id": connection_id,
                            "start_ns": prev,
                            "end_ns": received_at_ns,
                            "duration_seconds": round(gap_ns / 1_000_000_000, 3),
                            "kind": "silence",
                        }
                    )
            last_msg_ns[connection_id] = received_at_ns

            first_ns = first_seen_by_topo[str(topology_id)].observe(
                event_key=event_key, received_at_ns=received_at_ns
            )
            arrival_delta_ms = max(0.0, (received_at_ns - first_ns) / 1_000_000.0)

            venue_ns = rec.get("venue_timestamp_ns")
            freshness_ms: float | None = None
            if isinstance(venue_ns, int):
                freshness_ms = (received_at_ns - venue_ns) / 1_000_000.0

            in_warmup = bool(rec.get("in_warmup"))

            # Use absolute-ns / width as bucket index; translate to relative
            # at materialize time.
            b60 = received_at_ns // BUCKET_60S_NS
            b10 = received_at_ns // BUCKET_10S_NS

            row60 = buckets_60s.get((connection_id, b60))
            if row60 is None:
                row60 = _BucketAccum()
                buckets_60s[(connection_id, b60)] = row60
            row60.add(
                arrival_delta_ms=arrival_delta_ms,
                freshness_ms=freshness_ms,
                in_warmup=in_warmup,
            )

            row10 = buckets_10s.get((connection_id, b10))
            if row10 is None:
                row10 = _BucketAccum()
                buckets_10s[(connection_id, b10)] = row10
            row10.add(
                arrival_delta_ms=arrival_delta_ms,
                freshness_ms=freshness_ms,
                in_warmup=in_warmup,
            )

            offset = fh.tell()

    if run_started_ns is None:
        return {"empty": True, "skipped_lines": skipped_lines}
    if run_ended_ns is None:
        raise RuntimeError("events.jsonl had no usable rows")

    origin_60s = run_started_ns // BUCKET_60S_NS
    origin_10s = run_started_ns // BUCKET_10S_NS

    def _materialize(
        store: dict[tuple[str, int], _BucketAccum],
        origin: int,
    ) -> dict[str, list[list[Any]]]:
        out: dict[str, list[list[Any]]] = defaultdict(list)
        for (cid, idx), acc in store.items():
            rel_idx = int(idx - origin)
            insort(out[cid], acc.to_row(rel_idx), key=lambda r: r[0])
        return out

    rows_60s = _materialize(buckets_60s, origin_60s)
    rows_10s = _materialize(buckets_10s, origin_10s)

    return {
        "run_started_ns": run_started_ns,
        "run_ended_ns": run_ended_ns,
        "first_bucket_60s_start_ns": origin_60s * BUCKET_60S_NS,
        "first_bucket_10s_start_ns": origin_10s * BUCKET_10S_NS,
        "conn_meta": conn_meta,
        "gaps": gaps,
        "byte_index": byte_index,
        "line_count": line_count,
        "skipped_lines": skipped_lines,
        "rows_60s": rows_60s,
        "rows_10s": rows_10s,
    }


def _aggregate_connections(
    connections_path: Path,
    *,
    run_started_ns: int,
) -> list[dict[str, Any]]:
    """Diff successive connections.jsonl snapshots to emit transition events.

    connections.jsonl is sampled on `progress_interval_seconds`. We compare each
    snapshot to the previous one for the same connection_id and emit a
    transition event for every monotonic counter that ticked up.
    """
    if not connections_path.exists():
        return []

    last: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []

    with connections_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                snap = json.loads(raw)
            except Exception:
                continue
            cid = snap.get("connection_id")
            if not cid:
                continue
            recorded_at = snap.get("recorded_at")
            try:
                # ISO 8601 → ns. Parse with fromisoformat (Python 3.11+ accepts 'Z').
                from datetime import datetime, timezone

                if isinstance(recorded_at, str):
                    s = recorded_at.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    at_ns = int(dt.timestamp() * 1_000_000_000)
                else:
                    at_ns = run_started_ns
            except Exception:
                at_ns = run_started_ns

            prev = last.get(cid)
            if prev is not None:
                for kind, field_name in (
                    ("reconnect", "reconnects"),
                    ("disconnect", "disconnects"),
                    ("connect_failure", "connect_failures"),
                    ("market_rebind", "market_rebinds"),
                    ("rotation", "rotations"),
                    ("warmup_reset", "warmup_resets"),
                ):
                    delta = (snap.get(field_name) or 0) - (prev.get(field_name) or 0)
                    if delta > 0:
                        transitions.append(
                            {
                                "connection_id": cid,
                                "kind": kind,
                                "at_ns": at_ns,
                                "delta": delta,
                                "context": {
                                    "phase": snap.get("phase"),
                                    "elapsed_seconds": snap.get("elapsed_seconds"),
                                    "in_warmup": snap.get("in_warmup"),
                                    "current_segment_id": snap.get("current_segment_id"),
                                    "switch_reason": snap.get("switch_reason"),
                                    "last_error": snap.get("last_error"),
                                },
                            }
                        )
                # Track error transitions: last_error went from None to a string.
                prev_err = prev.get("last_error")
                cur_err = snap.get("last_error")
                if cur_err and cur_err != prev_err:
                    transitions.append(
                        {
                            "connection_id": cid,
                            "kind": "error",
                            "at_ns": at_ns,
                            "context": {
                                "last_error": cur_err,
                                "phase": snap.get("phase"),
                            },
                        }
                    )
            last[cid] = snap

    transitions.sort(key=lambda t: t["at_ns"])
    return transitions


def _resolve_summary_path(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        candidate = p / "summary.json"
        if not candidate.exists():
            raise FileNotFoundError(f"no summary.json in {p}")
        return candidate
    if p.suffix != ".json":
        raise ValueError(f"expected summary.json or run dir, got {p}")
    if not p.exists():
        raise FileNotFoundError(p)
    return p


def _load_event_retention(summary_path: Path) -> float:
    try:
        with summary_path.open("r", encoding="utf-8") as fh:
            summary = json.load(fh)
        cfg = summary.get("run_config", {}) or summary.get("run_metadata", {})
        v = cfg.get("event_retention_seconds")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    except Exception:
        pass
    return 30.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        help="Path to a recordings/ws-bench/<ts>/ directory or its summary.json",
    )
    parser.add_argument(
        "--index-stride",
        type=int,
        default=DEFAULT_INDEX_STRIDE,
        help=f"emit one byte-offset index entry every N events.jsonl lines (default {DEFAULT_INDEX_STRIDE})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite timeline/ even if it already looks up to date",
    )
    args = parser.parse_args(argv)

    summary_path = _resolve_summary_path(args.target)
    run_dir = summary_path.parent
    events_path = run_dir / "events.jsonl"
    connections_path = run_dir / "connections.jsonl"
    timeline_dir = run_dir / "timeline"
    index_path = timeline_dir / "index.json"

    if not events_path.exists():
        print(
            f"[timeline] no events.jsonl in {run_dir}; nothing to do "
            "(run benchmark with --write-event-log)",
            file=sys.stderr,
        )
        return 0

    if (
        not args.force
        and index_path.exists()
        and index_path.stat().st_mtime >= events_path.stat().st_mtime
    ):
        print(
            f"[timeline] {index_path} is up to date — pass --force to rebuild",
            file=sys.stderr,
        )
        return 0

    timeline_dir.mkdir(parents=True, exist_ok=True)
    (timeline_dir / "conn").mkdir(exist_ok=True)

    retention_seconds = _load_event_retention(summary_path)
    started_at = time.monotonic()
    print(
        f"[timeline] aggregating {events_path} (retention={retention_seconds}s, "
        f"index_stride={args.index_stride})",
        file=sys.stderr,
    )

    result = _aggregate_events(
        events_path,
        retention_seconds=retention_seconds,
        index_stride=args.index_stride,
    )
    if result.get("empty"):
        print(
            f"[timeline] events.jsonl was empty (skipped={result.get('skipped_lines')})",
            file=sys.stderr,
        )
        return 0

    transitions = _aggregate_connections(
        connections_path, run_started_ns=result["run_started_ns"]
    )

    # Write index.json
    connections_meta = sorted(
        result["conn_meta"].values(),
        key=lambda c: (c["topology_id"], c["connection_id"]),
    )
    topology_ids = sorted({c["topology_id"] for c in connections_meta}, key=int)

    with index_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "schema_version": SCHEMA_VERSION,
                "run_started_ns": result["run_started_ns"],
                "run_ended_ns": result["run_ended_ns"],
                "duration_seconds": (result["run_ended_ns"] - result["run_started_ns"]) / 1e9,
                "connections": connections_meta,
                "topologies": topology_ids,
                "event_retention_seconds": retention_seconds,
                "events_log": {
                    "filename": "events.jsonl",
                    "line_count": result["line_count"],
                    "skipped_lines": result["skipped_lines"],
                    "byte_offsets": result["byte_index"],
                    "stride": args.index_stride,
                },
                "bucket_widths_seconds": [60, 10],
                "gap_threshold_seconds": GAP_THRESHOLD_SECONDS,
                "generated_at": time.time(),
            },
            fh,
            separators=(",", ":"),
        )

    # Write buckets_60s.json (full overview)
    with (timeline_dir / "buckets_60s.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "bucket_size_ns": BUCKET_60S_NS,
                "first_bucket_start_ns": result["first_bucket_60s_start_ns"],
                "row_schema": [
                    "bucket_idx",
                    "msg_count",
                    "arrival_delta_p50_ms",
                    "arrival_delta_p95_ms",
                    "freshness_p95_ms",
                    "in_warmup",
                ],
                "rows_by_connection": result["rows_60s"],
            },
            fh,
            separators=(",", ":"),
        )

    # Write per-connection 10s shards
    conn_dir = timeline_dir / "conn"
    for cid, rows in result["rows_10s"].items():
        with (conn_dir / f"{cid}.10s.json").open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "connection_id": cid,
                    "bucket_size_ns": BUCKET_10S_NS,
                    "first_bucket_start_ns": result["first_bucket_10s_start_ns"],
                    "row_schema": [
                        "bucket_idx",
                        "msg_count",
                        "arrival_delta_p50_ms",
                        "arrival_delta_p95_ms",
                        "freshness_p95_ms",
                        "in_warmup",
                    ],
                    "rows": rows,
                },
                fh,
                separators=(",", ":"),
            )

    # Write transitions.json + gaps.json
    with (timeline_dir / "transitions.json").open("w", encoding="utf-8") as fh:
        json.dump({"transitions": transitions}, fh, separators=(",", ":"))
    with (timeline_dir / "gaps.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "gap_threshold_seconds": GAP_THRESHOLD_SECONDS,
                "gaps": result["gaps"],
            },
            fh,
            separators=(",", ":"),
        )

    elapsed = time.monotonic() - started_at
    print(
        f"[timeline] wrote {timeline_dir} "
        f"(events={result['line_count']:,} skipped={result['skipped_lines']:,} "
        f"connections={len(connections_meta)} transitions={len(transitions)} "
        f"gaps={len(result['gaps'])} elapsed={elapsed:.1f}s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
