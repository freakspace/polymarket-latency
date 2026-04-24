#!/usr/bin/env python3
"""Render a standalone report.html from a polymarket_order_burst summary.json.

Mirrors the Next.js OrderBurstReport view as a static single-file HTML document
with embedded CSS. No runtime dependencies beyond the Python stdlib.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


def _fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    if numeric != numeric:  # NaN
        return "—"
    if abs(numeric) >= 1000.0:
        return f"{numeric / 1000:.2f}s"
    return f"{numeric:.1f}ms"


def _fmt_percent(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{numeric * 100:.{digits}f}%"


def _fmt_signed_delta_ms(value: Optional[float]) -> str:
    """Match the TSX formatting: positive median means we beat baseline (show −)."""
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "−" if numeric >= 0 else "+"
    return f"{sign}{abs(numeric):.1f}ms"


def _escape(value: Any) -> str:
    return _html.escape("" if value is None else str(value))


def _repeat_runs(summary: dict[str, Any]) -> list[dict[str, Any]]:
    runs = summary.get("repeat_runs") or []
    if runs:
        return list(runs)
    results = summary.get("results") or []
    baseline = next(
        (r.get("summary", {}).get("fastest_success_ms") for r in results if r.get("fanout") == 1),
        None,
    )
    return [
        {
            "repeat_index": 1,
            "baseline_fastest_success_ms": baseline,
            "results": results,
        }
    ]


def _client_success_count(fanout_result: dict[str, Any]) -> int:
    count = fanout_result.get("client_success_count")
    if count is not None:
        return int(count)
    return sum(
        1
        for req in fanout_result.get("requests", [])
        if req.get("kind") == "success"
    )


def _duplicate_reject_count(fanout_result: dict[str, Any]) -> int:
    count = fanout_result.get("duplicate_reject_count")
    if count is not None:
        return int(count)
    return sum(
        1
        for req in fanout_result.get("requests", [])
        if req.get("kind") == "duplicate"
    )


def _transport_error_count(fanout_result: dict[str, Any]) -> int:
    count = fanout_result.get("transport_error_count")
    if count is not None:
        return int(count)
    return sum(
        1
        for req in fanout_result.get("requests", [])
        if req.get("kind") == "transport_error"
    )


def _winner_landed(fanout_result: dict[str, Any]) -> bool:
    value = fanout_result.get("winner_landed")
    if value is not None:
        return bool(value)
    return int(fanout_result.get("new_open_order_count", 0)) > 0


def _landed_without_success(fanout_result: dict[str, Any]) -> bool:
    value = fanout_result.get("landed_without_success_response")
    if value is not None:
        return bool(value)
    open_count = int(fanout_result.get("new_open_order_count", 0))
    success_count = _client_success_count(fanout_result)
    return open_count > 0 and success_count < open_count


def _aggregate_by_fanout(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = summary.get("aggregate_by_fanout") or []
    if rows:
        return list(rows)

    repeat_runs = _repeat_runs(summary)
    first_run = repeat_runs[0] if repeat_runs else {"results": []}
    baseline_fastest = first_run.get("baseline_fastest_success_ms")
    synthetic: list[dict[str, Any]] = []
    for r in first_run.get("results", []):
        client_success = _client_success_count(r)
        duplicate_total = _duplicate_reject_count(r)
        transport_total = _transport_error_count(r)
        winner_landed = _winner_landed(r)
        landed_wo_success = _landed_without_success(r)
        fastest = r.get("summary", {}).get("fastest_success_ms")
        comparable = baseline_fastest is not None and fastest is not None
        improvement = (
            float(baseline_fastest) - float(fastest) if comparable else None
        )
        synthetic.append(
            {
                "fanout": r.get("fanout"),
                "repeat_count": 1,
                "winner_landed_count": 1 if winner_landed else 0,
                "winner_landed_rate": 1.0 if winner_landed else 0.0,
                "client_success_repeat_count": 1 if client_success > 0 else 0,
                "client_success_rate": 1.0 if client_success > 0 else 0.0,
                "landed_without_success_response_count": 1 if landed_wo_success else 0,
                "landed_without_success_response_rate": 1.0 if landed_wo_success else 0.0,
                "duplicate_reject_total": duplicate_total,
                "transport_error_total": transport_total,
                "orders_landed_total": int(r.get("new_open_order_count", 0)),
                "observed_winner_latency_ms": {
                    "sample_count": 1 if fastest is not None else 0,
                    "min": fastest,
                    "median": fastest,
                    "max": fastest,
                },
                "improvement_vs_repeat_baseline_ms": {
                    "sample_count": 1 if improvement is not None else 0,
                    "min": improvement,
                    "median": improvement,
                    "max": improvement,
                },
                "comparable_repeat_count": 1 if comparable else 0,
                "beat_repeat_baseline_count": 1 if improvement is not None and improvement > 0 else 0,
                "beat_repeat_baseline_rate": 1.0 if improvement is not None and improvement > 0 else 0.0,
            }
        )
    return synthetic


def _totals(summary: dict[str, Any]) -> dict[str, int]:
    total_reqs = 0
    total_success = 0
    total_fanout_runs = 0
    total_winners = 0
    total_landed_wo_success = 0
    total_on_book = 0
    for repeat_run in _repeat_runs(summary):
        for r in repeat_run.get("results", []):
            reqs = r.get("requests", [])
            total_reqs += len(reqs)
            total_success += _client_success_count(r)
            total_fanout_runs += 1
            total_winners += 1 if _winner_landed(r) else 0
            total_landed_wo_success += 1 if _landed_without_success(r) else 0
            total_on_book += int(r.get("new_open_order_count", 0))
    return {
        "total_reqs": total_reqs,
        "total_success": total_success,
        "total_fanout_runs": total_fanout_runs,
        "total_winners": total_winners,
        "total_landed_wo_success": total_landed_wo_success,
        "total_on_book": total_on_book,
        "total_errors": total_reqs - total_success,
    }


_CSS = """
:root {
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
  --good: #059669;
  --warn: #b45309;
  --bad: #dc2626;
  --good-soft: #d1fae5;
  --warn-soft: #fef3c7;
  --bad-soft: #fee2e2;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.container { max-width: 1280px; margin: 0 auto; padding: 40px 32px 64px; }
.eyebrow {
  font-size: 11px; font-weight: 600; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--accent); margin-bottom: 6px;
}
.hero {
  display: flex; flex-direction: column; gap: 10px;
  padding-bottom: 24px; border-bottom: 1px solid var(--border); margin-bottom: 32px;
}
.hero-title {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 14px;
}
.hero-title h1 { margin: 0; font-size: 26px; font-weight: 700; letter-spacing: -0.01em; }
.hero-params { font-family: var(--mono); font-size: 12px; color: var(--text-subtle); }
.hero-meta { font-family: var(--mono); font-size: 12px; color: var(--text-muted); }
.hero-meta .label { color: var(--text-subtle); }
.hero-meta .token { word-break: break-all; }
.hero-meta .warn { color: var(--warn); margin-left: 4px; }
.kpi-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px; margin-bottom: 32px;
}
.kpi {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px;
}
.kpi-label {
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--text-subtle);
}
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-detail { font-size: 12px; color: var(--text-muted); }
.panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px 22px; margin-bottom: 24px;
}
.panel h2 { margin: 0 0 12px; font-size: 16px; font-weight: 600; }
.panel h3 { margin: 0 0 10px; font-size: 14px; font-weight: 600; }
.panel .panel-note {
  font-size: 12px; color: var(--text-subtle); margin-top: 10px;
}
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 13px; }
thead th {
  text-align: left; font-weight: 600; color: var(--text-subtle);
  font-family: "Inter", system-ui, sans-serif; font-size: 11px;
  letter-spacing: 0.08em; text-transform: uppercase;
  padding: 8px 12px 8px 0; border-bottom: 1px solid var(--border);
}
tbody td {
  padding: 8px 12px 8px 0; border-top: 1px solid var(--border);
  vertical-align: top;
}
tbody tr:first-child td { border-top: none; }
.subtle { color: var(--text-subtle); }
.muted { color: var(--text-muted); }
.truncate {
  max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kind-badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
}
.kind-success { background: var(--good-soft); color: var(--good); }
.kind-duplicate { background: var(--warn-soft); color: var(--warn); }
.kind-error, .kind-transport_error { background: var(--bad-soft); color: var(--bad); }
.kind-other { background: #f1f5f9; color: var(--text-subtle); }
.repeat-heading {
  font-size: 11px; font-weight: 600; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--text-subtle); margin: 0 0 10px;
}
.fanout-header {
  display: flex; flex-wrap: wrap; justify-content: space-between;
  gap: 8px; align-items: baseline; margin-bottom: 10px;
}
.fanout-header .fanout-label {
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--accent);
}
.fanout-header .fanout-meta {
  font-family: var(--mono); font-size: 12px; color: var(--text-muted);
}
.error-cell { color: var(--bad); }
.delta-better { color: var(--good); }
.delta-worse { color: var(--bad); }
"""


def _kpi_tile(label: str, value: str, detail: str = "") -> str:
    return (
        f"<div class='kpi'>"
        f"<div class='kpi-label'>{_escape(label)}</div>"
        f"<div class='kpi-value'>{_escape(value)}</div>"
        f"<div class='kpi-detail'>{_escape(detail)}</div>"
        f"</div>"
    )


def _aggregate_table(rows: Sequence[dict[str, Any]]) -> str:
    header = (
        "<tr>"
        "<th>Fanout</th>"
        "<th>Repeats</th>"
        "<th>Winner landed</th>"
        "<th>Client success</th>"
        "<th>Winner latency (median)</th>"
        "<th>Range</th>"
        "<th>Median Δ vs 1</th>"
        "<th>Beat 1</th>"
        "<th>Dupes</th>"
        "<th>Transport errors</th>"
        "<th>Landed w/o success</th>"
        "<th>On book</th>"
        "</tr>"
    )

    body_rows: list[str] = []
    for row in rows:
        fanout = row.get("fanout")
        repeat_count = int(row.get("repeat_count", 0) or 0)
        winner_landed = int(row.get("winner_landed_count", 0) or 0)
        winner_rate = row.get("winner_landed_rate")
        success_count = int(row.get("client_success_repeat_count", 0) or 0)
        success_rate = row.get("client_success_rate")
        latency = row.get("observed_winner_latency_ms") or {}
        improvement = row.get("improvement_vs_repeat_baseline_ms") or {}
        improvement_median = improvement.get("median")
        improvement_cell: str
        if fanout == 1:
            improvement_cell = "baseline"
        elif improvement_median is None:
            improvement_cell = "—"
        else:
            improvement_cell = _fmt_signed_delta_ms(improvement_median)
        body_rows.append(
            "<tr>"
            f"<td>{_escape(fanout)}</td>"
            f"<td>{_escape(repeat_count)}</td>"
            f"<td>{winner_landed}/{repeat_count} <span class='subtle'>({_escape(_fmt_percent(winner_rate, 0))})</span></td>"
            f"<td>{success_count}/{repeat_count} <span class='subtle'>({_escape(_fmt_percent(success_rate, 0))})</span></td>"
            f"<td>{_escape(_fmt_ms(latency.get('median')))}</td>"
            f"<td>{_escape(_fmt_ms(latency.get('min')))} <span class='subtle'>/</span> {_escape(_fmt_ms(latency.get('max')))}</td>"
            f"<td>{_escape(improvement_cell)}</td>"
            f"<td>{int(row.get('beat_repeat_baseline_count', 0) or 0)}/{int(row.get('comparable_repeat_count', 0) or 0)}</td>"
            f"<td>{int(row.get('duplicate_reject_total', 0) or 0)}</td>"
            f"<td>{int(row.get('transport_error_total', 0) or 0)}</td>"
            f"<td>{int(row.get('landed_without_success_response_count', 0) or 0)}/{repeat_count}</td>"
            f"<td>{int(row.get('orders_landed_total', 0) or 0)}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'>"
        "<table><thead>"
        + header
        + "</thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
        "</div>"
    )


def _request_table(requests: Sequence[dict[str, Any]]) -> str:
    header = (
        "<tr>"
        "<th>#</th>"
        "<th>Latency</th>"
        "<th>Kind</th>"
        "<th>Status</th>"
        "<th>Order ID</th>"
        "<th>Error</th>"
        "</tr>"
    )
    sorted_reqs = sorted(
        requests, key=lambda req: int(req.get("index", 0) or 0)
    )
    rows: list[str] = []
    for req in sorted_reqs:
        kind_raw = str(req.get("kind") or "other")
        kind_class = (
            f"kind-{kind_raw}"
            if kind_raw in {"success", "duplicate", "error", "transport_error"}
            else "kind-other"
        )
        rows.append(
            "<tr>"
            f"<td class='subtle'>#{_escape(req.get('index'))}</td>"
            f"<td>{_escape(_fmt_ms(req.get('latency_ms')))}</td>"
            f"<td><span class='kind-badge {kind_class}'>{_escape(kind_raw)}</span></td>"
            f"<td>{_escape(req.get('status') or '—')}</td>"
            f"<td class='truncate'>{_escape(req.get('order_id') or '—')}</td>"
            f"<td class='error-cell'>{_escape(req.get('error') or '')}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'>"
        "<table><thead>"
        + header
        + "</thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "</div>"
    )


def _fanout_detail_panel(fanout_result: dict[str, Any]) -> str:
    counts = fanout_result.get("summary", {}).get("request_counts", {}) or {}
    count_parts = " · ".join(
        f"{_escape(k)}={_escape(v)}" for k, v in sorted(counts.items())
    )
    success_count = _client_success_count(fanout_result)
    request_count = len(fanout_result.get("requests", []))
    winner_landed = _winner_landed(fanout_result)
    landed_wo_success = _landed_without_success(fanout_result)
    on_book = int(fanout_result.get("new_open_order_count", 0) or 0)
    return (
        "<section class='panel'>"
        "<div class='fanout-header'>"
        "<div>"
        f"<div class='fanout-label'>fanout = {_escape(fanout_result.get('fanout'))}"
        f" <span class='subtle'>· shared_ts {_escape(fanout_result.get('shared_timestamp_ms'))}</span>"
        "</div>"
        f"<div class='muted' style='font-family: var(--mono); font-size:12px;'>{count_parts}</div>"
        "</div>"
        "<div class='fanout-meta'>"
        f"client success: {success_count}/{request_count}"
        f" <span class='subtle'>·</span> winner landed: {'yes' if winner_landed else 'no'}"
        f" <span class='subtle'>·</span> landed w/o success: {'yes' if landed_wo_success else 'no'}"
        f" <span class='subtle'>·</span> on book: {on_book}/{request_count}"
        "</div>"
        "</div>"
        + _request_table(fanout_result.get("requests", []))
        + "</section>"
    )


def render_report_html(summary: dict[str, Any], *, timestamp: str) -> str:
    repeat_runs = _repeat_runs(summary)
    aggregate_rows = _aggregate_by_fanout(summary)
    totals = _totals(summary)
    repeats = int(summary.get("repeats") or len(repeat_runs) or 1)

    kpis_html = "".join(
        [
            _kpi_tile("Repeats", str(repeats)),
            _kpi_tile("Requests", f"{totals['total_reqs']:,}"),
            _kpi_tile(
                "Client Success",
                f"{totals['total_success']}/{totals['total_reqs']}",
                _fmt_percent(
                    totals["total_success"] / totals["total_reqs"]
                    if totals["total_reqs"]
                    else 0.0,
                    0,
                ),
            ),
            _kpi_tile(
                "Winner Landed",
                f"{totals['total_winners']}/{totals['total_fanout_runs']}",
                "fanouts with at least one order on book",
            ),
            _kpi_tile(
                "Errors",
                str(totals["total_errors"]),
                "clean" if totals["total_errors"] == 0 else "see per-fanout table",
            ),
            _kpi_tile(
                "Orders landed",
                str(totals["total_on_book"]),
                "seen on book post-burst",
            ),
            _kpi_tile(
                "Landed Without Success",
                str(totals["total_landed_wo_success"]),
                "winner landed, but client saw no matching success",
            ),
        ]
    )

    hero_params = (
        f"{_escape(summary.get('side'))} · price {_escape(summary.get('price'))} · size {_escape(summary.get('size'))}"
        f" · post_only={_escape(summary.get('post_only'))} · cleanup={_escape(summary.get('cleanup'))}"
        f" · burst_mode={_escape(summary.get('burst_mode') or 'exact-duplicate')}"
    )
    resolved_chain = summary.get("resolved_chain_id")
    chain_id = summary.get("chain_id")
    resolved_note = (
        f"<span class='warn'>(resolved {_escape(resolved_chain)})</span>"
        if resolved_chain is not None and resolved_chain != chain_id
        else ""
    )

    per_repeat_sections: list[str] = []
    multiple_repeats = len(repeat_runs) > 1
    for repeat_run in repeat_runs:
        heading = (
            f"<div class='repeat-heading'>repeat {_escape(repeat_run.get('repeat_index'))}/{len(repeat_runs)}</div>"
            if multiple_repeats
            else ""
        )
        fanout_blocks = "".join(
            _fanout_detail_panel(r) for r in repeat_run.get("results", [])
        )
        per_repeat_sections.append(f"<section>{heading}{fanout_blocks}</section>")

    title = f"Order burst · {timestamp}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_escape(title)}</title>
  <style>{_CSS}</style>
</head>
<body>
  <main class="container">
    <header class="hero">
      <div class="eyebrow">Order Burst</div>
      <div class="hero-title">
        <h1>{_escape(timestamp)}</h1>
        <span class="hero-params">{hero_params}</span>
      </div>
      <div class="hero-meta">
        <div class="token"><span class="label">token: </span>{_escape(summary.get('token_id'))}</div>
        <div>
          <span class="label">host: </span>{_escape(summary.get('host'))}
          <span class="subtle">·</span>
          <span class="label">chain_id: </span>{_escape(chain_id)}{resolved_note}
          <span class="subtle">·</span>
          <span class="label">counts: </span>{_escape(', '.join(str(c) for c in summary.get('counts') or []))}
        </div>
      </div>
    </header>

    <section class="kpi-row">
      {kpis_html}
    </section>

    <section class="panel">
      <h2>Per-fanout summary</h2>
      {_aggregate_table(aggregate_rows)}
      <div class="panel-note">
        Winner latency uses the fastest successful client response seen in a repeat.
        Δ is measured against the same repeat&rsquo;s fanout=1 baseline.
        Negative Δ (shown as &minus;) means the larger fanout was faster than fanout=1.
      </div>
    </section>

    {''.join(per_repeat_sections)}
  </main>
</body>
</html>
"""


def load_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("summary file must contain a top-level JSON object")
    for key in ("token_id", "side"):
        if key not in payload:
            raise ValueError(f"summary file missing required key: {key}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a standalone report.html from a polymarket_order_burst summary.json."
    )
    parser.add_argument(
        "summary",
        type=Path,
        help="Path to summary.json produced by polymarket_order_burst.py",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Path to write report.html. Defaults to report.html next to summary.json.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    summary_path = args.summary.expanduser().resolve()
    if not summary_path.is_file():
        print(f"[ERROR] summary file not found: {summary_path}", file=sys.stderr)
        return 1

    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else summary_path.with_name("report.html")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        summary = load_summary(summary_path)
    except Exception as exc:
        print(f"[ERROR] failed to load summary {summary_path}: {exc}", file=sys.stderr)
        return 1

    timestamp = summary_path.parent.name
    html_text = render_report_html(summary, timestamp=timestamp)
    output_path.write_text(html_text, encoding="utf-8")
    print(f"[report] wrote: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
