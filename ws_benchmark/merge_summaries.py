#!/usr/bin/env python3
"""Merge multiple per-topology benchmark summaries into a unified report.

Reads ``topology-*ws/summary.json`` files under the given root and produces
a unified ``summary.json`` plus ``report.html`` at the root. The unified
report has the same top-level shape as a single-process multi-topology run,
which means the existing html_generator can render it.

Caveats baked into the merged report:
- Cross-topology arrival_delta is within-process only. Each process saw
  only its own topology, so first_seen_win_rate per topology is always
  ~100% within its own scope and is not directly comparable across pools
  in this merged report.
- Headline questions (Q1/Q2/Q3 in the rendered report) are recomputed
  by html_generator from the merged topologies array, which is fine for
  freshness/coverage/gap comparisons but not for the racing-arrival metric.

Usage:
  merge_summaries.py <output-root>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _topology_sort_key(item: tuple[str, dict[str, Any]]) -> int:
    key, _ = item
    try:
        return int(key)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: merge_summaries.py <root-dir>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"[merge] not a directory: {root}", file=sys.stderr)
        return 1

    topology_dirs = sorted(
        d
        for d in root.iterdir()
        if d.is_dir() and d.name.startswith("topology-")
    )
    if not topology_dirs:
        print(f"[merge] no topology-*/ subdirs under {root}", file=sys.stderr)
        return 1

    loaded: list[tuple[Path, dict[str, Any]]] = []
    for d in topology_dirs:
        summary_file = d / "summary.json"
        if not summary_file.exists():
            print(f"[merge] warning: missing {summary_file}", file=sys.stderr)
            continue
        try:
            payload = json.loads(summary_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[merge] warning: invalid JSON in {summary_file}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            print(f"[merge] warning: non-object summary in {summary_file}", file=sys.stderr)
            continue
        loaded.append((d, payload))

    if not loaded:
        print("[merge] no usable summaries found", file=sys.stderr)
        return 1

    print(f"[merge] read {len(loaded)} per-topology summaries")

    base_dir, base = loaded[0]
    merged: dict[str, Any] = json.loads(json.dumps(base))  # deep copy

    # `topologies` and `connections` in summary.json are both dicts keyed by id,
    # not lists. Merge dict-by-dict, warning on collisions.
    all_topologies: dict[str, dict[str, Any]] = {}
    all_connections: dict[str, Any] = {}

    for d, payload in loaded:
        topologies_dict = payload.get("topologies") or {}
        if not isinstance(topologies_dict, dict):
            print(
                f"[merge] warning: {d.name}/summary.json has non-dict topologies field; skipping",
                file=sys.stderr,
            )
            continue
        for tid, topo in topologies_dict.items():
            if tid in all_topologies:
                print(
                    f"[merge] warning: duplicate topology_id {tid!r} from {d.name} — keeping first",
                    file=sys.stderr,
                )
                continue
            all_topologies[tid] = topo

        connections = payload.get("connections") or {}
        if not isinstance(connections, dict):
            continue
        for conn_id, conn_payload in connections.items():
            if conn_id in all_connections:
                print(
                    f"[merge] warning: duplicate connection_id {conn_id!r} — keeping first",
                    file=sys.stderr,
                )
                continue
            all_connections[conn_id] = conn_payload

    sorted_items = sorted(all_topologies.items(), key=_topology_sort_key)
    sorted_topology_ids = [
        int(tid) for tid, _ in sorted_items if tid.isdigit()
    ]
    sorted_topologies_dict: dict[str, dict[str, Any]] = {
        tid: topo for tid, topo in sorted_items
    }

    merged["topologies"] = sorted_topologies_dict
    merged["connections"] = all_connections

    run_metadata = merged.setdefault("run_metadata", {})
    run_metadata["topologies"] = sorted_topology_ids
    run_metadata["multi_process"] = True
    run_metadata["multi_process_dirs"] = [d.name for d, _ in loaded]

    caveats = list(merged.get("caveats") or [])
    caveats.append(
        "This run was assembled from multiple per-topology processes "
        "(see run_metadata.multi_process_dirs). Within-process arrival_delta "
        "and first_seen metrics are not directly comparable across pools — "
        "each process is its own race winner by definition. Pool-level "
        "freshness, coverage, gap counts, and per-socket stall stats remain "
        "fully comparable across topologies."
    )
    merged["caveats"] = caveats

    summary_path = root / "summary.json"
    summary_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"[merge] wrote {summary_path}")

    repo_root = Path(__file__).resolve().parent.parent
    html_gen = repo_root / "ws_benchmark" / "html_generator.py"
    if not html_gen.exists():
        print(f"[merge] html_generator.py not found at {html_gen}", file=sys.stderr)
        return 1

    print("[merge] rendering report.html")
    proc = subprocess.run(
        [sys.executable, str(html_gen), str(summary_path)],
        cwd=str(repo_root),
    )
    if proc.returncode != 0:
        print(
            f"[merge] warning: html_generator returned {proc.returncode}; "
            f"summary.json is still usable at {summary_path}",
            file=sys.stderr,
        )
        return proc.returncode

    print(f"[merge] wrote {root / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
