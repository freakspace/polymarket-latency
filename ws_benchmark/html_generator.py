#!/usr/bin/env python3
"""
Generate HTML/SVG benchmark report artifacts from summary.json.

This is a post-run renderer for benchmark outputs. It writes report.html plus
chart SVGs into the selected output directory. If events.jsonl is available, it
also renders the connection timeline and topology gap timeline charts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from benchmark import write_visual_artifacts


def status_log(message: str) -> None:
    print(message, flush=True)


def load_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("summary file must contain a top-level JSON object")

    missing = [
        key
        for key in ("run_metadata", "topologies", "connections")
        if key not in payload
    ]
    if missing:
        raise ValueError(
            f"summary file is missing required keys: {', '.join(missing)}"
        )
    return payload


def default_events_path(summary_path: Path) -> Optional[Path]:
    candidate = summary_path.with_name("events.jsonl")
    return candidate if candidate.is_file() else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate HTML/SVG report artifacts from a benchmark summary.json file."
    )
    parser.add_argument(
        "summary",
        type=Path,
        help="Path to summary.json produced by ws_benchmark/benchmark.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to write report.html and charts. Defaults to the summary.json directory.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        help="Optional path to events.jsonl. Defaults to a sibling events.jsonl next to summary.json when present.",
    )
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="Skip events.jsonl loading even if a sibling file exists.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    summary_path = args.summary.expanduser().resolve()
    if not summary_path.is_file():
        print(f"[ERROR] summary file not found: {summary_path}", file=sys.stderr)
        return 1

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else summary_path.parent
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    events_path: Optional[Path] = None
    if not args.no_events:
        if args.events is not None:
            events_path = args.events.expanduser().resolve()
            if not events_path.is_file():
                print(f"[ERROR] events file not found: {events_path}", file=sys.stderr)
                return 1
        else:
            events_path = default_events_path(summary_path)

    try:
        summary = load_summary(summary_path)
    except Exception as exc:
        print(f"[ERROR] failed to load summary {summary_path}: {exc}", file=sys.stderr)
        return 1

    status_log(f"[report] loaded summary: {summary_path}")
    status_log(f"[report] writing artifacts to: {output_dir}")
    if events_path is not None:
        status_log(f"[report] using events log: {events_path}")
    else:
        status_log(
            "[report] no events log selected; timeline charts will be omitted"
        )

    try:
        artifacts = write_visual_artifacts(summary, output_dir, events_path)
    except Exception as exc:
        print(f"[ERROR] report generation failed: {exc}", file=sys.stderr)
        return 1

    status_log(f"[report] HTML report: {artifacts['report_html']}")
    status_log(f"[report] charts written: {len(artifacts['charts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
