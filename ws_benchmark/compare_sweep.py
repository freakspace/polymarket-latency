#!/usr/bin/env python3
"""Compare headline metrics across a bench-sweep run.

Given the root of a sweep (e.g. recordings/ws-bench-sweeps/20260424_032107),
scans every rotation_*/summary.json inside it and prints a side-by-side table
of Freshness P95, Arrival P95, and drift-slope per topology per variant.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


def _load_summary(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _metric(summary: dict[str, Any], topology_id: str, name: str, field: str = "p95") -> Optional[float]:
    topologies = summary.get("topologies", {}) or {}
    row = topologies.get(topology_id) or {}
    dist = row.get(name) or {}
    value = dist.get(field)
    return float(value) if value is not None else None


def _drift_series(summary: dict[str, Any], topology_id: str) -> list[Optional[float]]:
    drift = summary.get("freshness_drift") or {}
    topologies = drift.get("topologies") or {}
    rows = topologies.get(topology_id) or []
    return [
        (row.get("freshness_ms") or {}).get("p95")
        for row in rows
    ]


def _drift_slope_ms_per_bucket(series: list[Optional[float]]) -> Optional[float]:
    """Simple first-vs-last slope across non-null buckets. Positive = degrading."""
    populated = [v for v in series if v is not None]
    if len(populated) < 2:
        return None
    return (populated[-1] - populated[0]) / (len(populated) - 1)


def _fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "  —  "
    if abs(value) >= 1000:
        return f"{value / 1000:>6.2f}s"
    return f"{value:>6.1f}ms"


def _fmt_delta_ms(value: Optional[float]) -> str:
    if value is None:
        return "  —   "
    sign = "+" if value >= 0 else "−"
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{sign}{magnitude / 1000:>5.2f}s"
    return f"{sign}{magnitude:>5.1f}ms"


def _variant_sort_key(name: str) -> tuple[int, float]:
    # "rotation_none" first, then numeric ascending so the comparison reads
    # left-to-right as "baseline" -> "most aggressive rotation".
    suffix = name.split("_", 1)[1] if "_" in name else name
    if suffix == "none":
        return (0, 0.0)
    try:
        return (1, float(suffix))
    except ValueError:
        return (2, float("inf"))


def compare_sweep(sweep_root: Path) -> int:
    variants: list[tuple[str, dict[str, Any]]] = []
    for child in sorted(sweep_root.iterdir(), key=lambda p: _variant_sort_key(p.name)):
        if not child.is_dir():
            continue
        summary_path = child / "summary.json"
        if not summary_path.is_file():
            continue
        summary = _load_summary(summary_path)
        if summary is None:
            print(f"[warn] could not read {summary_path}", file=sys.stderr)
            continue
        variants.append((child.name, summary))

    if not variants:
        print(f"[error] no rotation_*/summary.json found under {sweep_root}", file=sys.stderr)
        return 1

    topology_ids = sorted(
        {tid for _, s in variants for tid in (s.get("topologies") or {}).keys()},
        key=int,
    )

    run_metadata = variants[0][1].get("run_metadata", {}) or {}
    duration = run_metadata.get("duration_seconds")
    market = run_metadata.get("market_slug")
    drift_bucket = run_metadata.get("freshness_drift_bucket_seconds")

    print(f"=== Sweep comparison: {sweep_root} ===")
    if market:
        print(f"Market: {market}")
    if duration:
        print(f"Duration per run: {duration:.0f}s")
    if drift_bucket:
        print(f"Drift bucket width: {drift_bucket:.0f}s")
    print(f"Variants: {', '.join(label for label, _ in variants)}")
    print()

    _print_section(
        "Freshness P95 (ms)",
        variants,
        topology_ids,
        lambda s, t: _metric(s, t, "freshness_ms"),
        _fmt_ms,
    )
    _print_section(
        "Arrival P95 (ms)",
        variants,
        topology_ids,
        lambda s, t: _metric(s, t, "arrival_delta_ms"),
        _fmt_ms,
    )
    _print_section(
        "Coverage (%)",
        variants,
        topology_ids,
        lambda s, t: 100.0 * (s.get("topologies", {}).get(t) or {}).get("coverage_rate", 0.0),
        lambda v: f"{v:>6.2f}%" if v is not None else "  —   ",
    )
    _print_drift_table(variants, topology_ids)
    _print_rotation_health(variants)
    _print_verdict(variants, topology_ids)
    return 0


def _print_section(
    title: str,
    variants: list[tuple[str, dict[str, Any]]],
    topology_ids: list[str],
    getter,
    formatter,
) -> None:
    header = f"{title:<26}" + "".join(f"  {label:>14}" for label, _ in variants)
    print(header)
    print("-" * len(header))
    baseline_label, baseline_summary = variants[0]
    for topology_id in topology_ids:
        row_values = [getter(summary, topology_id) for _, summary in variants]
        baseline_value = row_values[0]
        cells = []
        for idx, value in enumerate(row_values):
            text = formatter(value) if value is not None else "  —   "
            if idx > 0 and value is not None and baseline_value is not None:
                delta_ratio = value / baseline_value - 1.0 if baseline_value else None
                if delta_ratio is not None:
                    marker = "↓" if value < baseline_value else ("↑" if value > baseline_value else "=")
                    text = f"{text} {marker}{abs(delta_ratio) * 100:>4.0f}%"
                else:
                    text = f"{text}     "
            cells.append(f"  {text:>14}")
        print(f"  {topology_id + ' ws':<24}" + "".join(cells))
    print()


def _print_drift_table(
    variants: list[tuple[str, dict[str, Any]]],
    topology_ids: list[str],
) -> None:
    title = "Drift slope (ms/bucket)"
    header = f"{title:<26}" + "".join(f"  {label:>14}" for label, _ in variants)
    print(header)
    print("-" * len(header))
    for topology_id in topology_ids:
        cells = []
        for _, summary in variants:
            slope = _drift_slope_ms_per_bucket(_drift_series(summary, topology_id))
            cells.append(f"  {_fmt_delta_ms(slope):>14}")
        print(f"  {topology_id + ' ws':<24}" + "".join(cells))
    print("(positive = degrading over the run; negative = improving; ~0 = steady state)")
    print()


def _print_rotation_health(variants: list[tuple[str, dict[str, Any]]]) -> None:
    title = "Rotation/connect health"
    header = f"{title:<26}" + "".join(f"  {label:>14}" for label, _ in variants)
    print(header)
    print("-" * len(header))
    metrics = [
        ("rotations total", lambda s: sum(c.get("rotations", 0) or 0 for c in (s.get("connections") or {}).values())),
        ("connect_failures", lambda s: sum(c.get("connect_failures", 0) or 0 for c in (s.get("connections") or {}).values())),
        ("unplanned disconnects", lambda s: sum(c.get("disconnects", 0) or 0 for c in (s.get("connections") or {}).values())),
    ]
    for label, getter in metrics:
        cells = [f"  {getter(summary):>14,}" for _, summary in variants]
        print(f"  {label:<24}" + "".join(cells))
    print()


def _print_verdict(
    variants: list[tuple[str, dict[str, Any]]],
    topology_ids: list[str],
) -> None:
    if len(variants) < 2:
        return
    print("=== Headline comparison (10 ws, highest-topology pool) ===")
    target_topology = topology_ids[-1] if topology_ids else None
    if target_topology is None:
        return
    baseline_label, baseline_summary = variants[0]
    baseline_fresh = _metric(baseline_summary, target_topology, "freshness_ms")
    baseline_slope = _drift_slope_ms_per_bucket(_drift_series(baseline_summary, target_topology))

    print(f"Baseline: {baseline_label}")
    print(f"  Freshness P95 = {_fmt_ms(baseline_fresh)}")
    print(f"  Drift slope   = {_fmt_delta_ms(baseline_slope)} per bucket")
    print()

    for label, summary in variants[1:]:
        fresh = _metric(summary, target_topology, "freshness_ms")
        slope = _drift_slope_ms_per_bucket(_drift_series(summary, target_topology))
        if fresh is not None and baseline_fresh is not None:
            delta = fresh - baseline_fresh
            delta_str = f"{_fmt_delta_ms(delta)} vs baseline"
        else:
            delta_str = "  (insufficient data)"
        print(f"{label}: Freshness P95 = {_fmt_ms(fresh)}  {delta_str}")
        print(f"          Drift slope   = {_fmt_delta_ms(slope)} per bucket")
    print()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sweep_root",
        type=Path,
        help="Path to recordings/ws-bench-sweeps/<timestamp>",
    )
    args = parser.parse_args(argv)
    root = args.sweep_root.expanduser().resolve()
    if not root.is_dir():
        print(f"[error] not a directory: {root}", file=sys.stderr)
        return 1
    return compare_sweep(root)


if __name__ == "__main__":
    raise SystemExit(main())
