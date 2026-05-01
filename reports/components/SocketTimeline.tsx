"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BucketRow,
  BucketsShard,
  ColorMode,
  Gap,
  TimelineBundle,
  Transition,
  colorFor,
} from "@/lib/timeline";
import { topologyColor } from "@/lib/palette";

const ROW_HEIGHT = 28;
const ROW_GAP = 1;
const LEFT_GUTTER = 220;
const BOTTOM_AXIS = 30;
const TOP_PADDING = 8;
const RIGHT_PADDING = 16;

// Switch from 60s overview to 10s shards when the visible window is below this.
const SHARD_THRESHOLD_SECONDS = 4 * 3600;

type ZoomRange = { startNs: number; endNs: number };

type HoverState = {
  connectionId: string;
  timeNs: number;
  row?: BucketRow;
};

export function SocketTimeline({
  timestamp,
  bundle,
  onSelectWindow,
}: {
  timestamp: string;
  bundle: TimelineBundle;
  onSelectWindow: (connectionId: string, startNs: number, endNs: number) => void;
}) {
  const { index, overview, transitions, gaps } = bundle;
  const [zoom, setZoom] = useState<ZoomRange>({
    startNs: index.run_started_ns,
    endNs: index.run_ended_ns,
  });
  const [colorMode, setColorMode] = useState<ColorMode>("arrival_delta");
  const [shardCache, setShardCache] = useState<Record<string, BucketsShard>>({});
  const [hover, setHover] = useState<HoverState | null>(null);
  const [brush, setBrush] = useState<{ startX: number; endX: number } | null>(null);

  const visibleSeconds = (zoom.endNs - zoom.startNs) / 1e9;
  const useShards = visibleSeconds <= SHARD_THRESHOLD_SECONDS;

  // Lazy-load 10s shards for connections we don't have yet, when zoomed in.
  useEffect(() => {
    if (!useShards) return;
    let cancelled = false;
    const missing = index.connections
      .map((c) => c.connection_id)
      .filter((cid) => !shardCache[cid]);
    if (missing.length === 0) return;

    (async () => {
      const fetched: Record<string, BucketsShard> = {};
      for (const cid of missing) {
        try {
          const r = await fetch(
            `/api/timeline-shard/${encodeURIComponent(timestamp)}/${encodeURIComponent(cid)}`,
            { cache: "force-cache" },
          );
          if (!r.ok) continue;
          fetched[cid] = (await r.json()) as BucketsShard;
        } catch {
          // ignore
        }
      }
      if (cancelled) return;
      setShardCache((prev) => ({ ...prev, ...fetched }));
    })();
    return () => {
      cancelled = true;
    };
  }, [useShards, index.connections, shardCache, timestamp]);

  // Pick rows to render (overview vs shard) per connection.
  const rowsForConnection = useCallback(
    (connectionId: string): { rows: BucketRow[]; bucketSeconds: number; firstNs: number } => {
      if (useShards && shardCache[connectionId]) {
        const s = shardCache[connectionId];
        return {
          rows: s.rows,
          bucketSeconds: s.bucket_size_ns / 1e9,
          firstNs: s.first_bucket_start_ns,
        };
      }
      return {
        rows: overview.rows_by_connection[connectionId] ?? [],
        bucketSeconds: overview.bucket_size_ns / 1e9,
        firstNs: overview.first_bucket_start_ns,
      };
    },
    [useShards, shardCache, overview],
  );

  const handleResetZoom = () => {
    setZoom({ startNs: index.run_started_ns, endNs: index.run_ended_ns });
  };

  return (
    <section className="mb-8">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow text-accent">Per-socket timeline</div>
          <h2 className="mt-2 font-serif text-[28px] font-bold leading-tight tracking-tight text-text">
            Connection swimlane.
          </h2>
          <p className="mt-1 max-w-[80ch] text-[13px] leading-relaxed text-text-muted">
            One row per WebSocket connection. Each cell is a {Math.round(overview.bucket_size_ns / 1e9)}-second
            (or {useShards ? 10 : Math.round(overview.bucket_size_ns / 1e9)}-second when zoomed)
            window; colour encodes the chosen metric. Drag to zoom into a window;
            click a cell to see the raw events that landed there.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <ColorModeToggle mode={colorMode} onChange={setColorMode} />
          <button
            type="button"
            onClick={handleResetZoom}
            className="font-mono text-xxs uppercase tracking-wider text-text-subtle transition-colors hover:text-text"
          >
            ↺ reset zoom
          </button>
        </div>
      </header>

      <div className="rounded-xl border border-border bg-bg-surface p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <SwimlaneCanvas
          connections={index.connections}
          rowsForConnection={rowsForConnection}
          transitions={transitions}
          gaps={gaps}
          zoom={zoom}
          colorMode={colorMode}
          brush={brush}
          setBrush={setBrush}
          setZoom={setZoom}
          onHover={setHover}
          onSelectCell={(cid, startNs, endNs) => onSelectWindow(cid, startNs, endNs)}
        />
        <Legend mode={colorMode} />
      </div>

      <div className="mt-2 min-h-[20px] font-mono text-[11px] text-text-subtle">
        {hover ? (
          <HoverDetails hover={hover} runStartedNs={index.run_started_ns} />
        ) : (
          <span>hover a cell for details · drag to zoom · click to inspect raw events</span>
        )}
      </div>
    </section>
  );
}

function HoverDetails({
  hover,
  runStartedNs,
}: {
  hover: HoverState;
  runStartedNs: number;
}) {
  const elapsedS = ((hover.timeNs - runStartedNs) / 1e9).toFixed(1);
  const wall = new Date(hover.timeNs / 1e6).toISOString().replace("T", " ").slice(0, 19);
  const r = hover.row;
  return (
    <span>
      <span className="text-text">{hover.connectionId}</span>
      {" · "}
      <span>{wall}</span>{" "}
      <span className="text-text-subtle">(t+{elapsedS}s)</span>
      {r ? (
        <>
          {" · "}
          msgs={r[1]} · arrival_p95={r[3] !== null ? `${r[3].toFixed(0)}ms` : "—"} ·
          fresh_p95={r[4] !== null ? `${r[4].toFixed(0)}ms` : "—"}
          {r[5] === 1 ? " · warmup" : ""}
        </>
      ) : (
        <> · empty bucket</>
      )}
    </span>
  );
}

function ColorModeToggle({
  mode,
  onChange,
}: {
  mode: ColorMode;
  onChange: (m: ColorMode) => void;
}) {
  const options: { value: ColorMode; label: string }[] = [
    { value: "arrival_delta", label: "Δ vs leader" },
    { value: "freshness", label: "freshness" },
    { value: "msg_rate", label: "msg rate" },
  ];
  return (
    <div className="flex gap-1 rounded-md border border-border bg-bg-base p-0.5 font-mono text-xxs">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={
            "rounded-sm px-2 py-1 transition-colors " +
            (mode === o.value
              ? "bg-text text-bg-base"
              : "text-text-subtle hover:text-text")
          }
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Legend({ mode }: { mode: ColorMode }) {
  const items: { color: string; label: string }[] =
    mode === "arrival_delta"
      ? [
          { color: "#16a34a", label: "≤5ms" },
          { color: "#65a30d", label: "≤50ms" },
          { color: "#ca8a04", label: "≤200ms" },
          { color: "#dc2626", label: "≤1s" },
          { color: "#b91c1c", label: "≤2s" },
          { color: "#7f1d1d", label: ">2s" },
        ]
      : mode === "freshness"
        ? [
            { color: "#16a34a", label: "≤100ms" },
            { color: "#65a30d", label: "≤250ms" },
            { color: "#ca8a04", label: "≤500ms" },
            { color: "#dc2626", label: "≤1s" },
            { color: "#b91c1c", label: "≤5s" },
            { color: "#7f1d1d", label: ">5s" },
          ]
        : [
            { color: "#475569", label: "<1/s" },
            { color: "#0369a1", label: "<5/s" },
            { color: "#0284c7", label: "<20/s" },
            { color: "#0ea5e9", label: "<50/s" },
            { color: "#38bdf8", label: "≥50/s" },
          ];
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 font-mono text-xxs text-text-subtle">
      <span>legend:</span>
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-3 rounded-[2px]"
            style={{ background: it.color }}
          />
          {it.label}
        </span>
      ))}
      <span className="ml-3 flex items-center gap-1">
        <span
          className="inline-block h-3 w-3 rounded-[2px]"
          style={{ background: "#1f2937" }}
        />
        empty
      </span>
      <span className="ml-3 flex items-center gap-1">
        <span className="inline-block h-3 w-1 bg-black" /> reconnect
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-1 border border-black bg-transparent" />
        disconnect
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-[2px] bg-red-500/40" />
        gap &gt;2s
      </span>
    </div>
  );
}

function SwimlaneCanvas({
  connections,
  rowsForConnection,
  transitions,
  gaps,
  zoom,
  colorMode,
  brush,
  setBrush,
  setZoom,
  onHover,
  onSelectCell,
}: {
  connections: { connection_id: string; topology_id: string }[];
  rowsForConnection: (connectionId: string) => {
    rows: BucketRow[];
    bucketSeconds: number;
    firstNs: number;
  };
  transitions: Transition[];
  gaps: Gap[];
  zoom: ZoomRange;
  colorMode: ColorMode;
  brush: { startX: number; endX: number } | null;
  setBrush: (b: { startX: number; endX: number } | null) => void;
  setZoom: (z: ZoomRange) => void;
  onHover: (h: HoverState | null) => void;
  onSelectCell: (cid: string, startNs: number, endNs: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);
  const dragStateRef = useRef<{ startX: number; lastX: number } | null>(null);

  // Group connections by topology so the swimlane stacks topology-by-topology.
  const ordered = useMemo(() => {
    const byTopo: Record<string, typeof connections> = {};
    for (const c of connections) {
      (byTopo[c.topology_id] ||= []).push(c);
    }
    const topologies = Object.keys(byTopo).sort((a, b) => Number(a) - Number(b));
    return topologies.flatMap((t) => byTopo[t]);
  }, [connections]);

  const height = TOP_PADDING + ordered.length * (ROW_HEIGHT + ROW_GAP) + BOTTOM_AXIS;

  // Resize observer to size to container.
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.max(640, Math.floor(entry.contentRect.width));
      setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Drawing.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const plotLeft = LEFT_GUTTER;
    const plotRight = width - RIGHT_PADDING;
    const plotWidth = plotRight - plotLeft;

    const span = zoom.endNs - zoom.startNs;
    if (span <= 0 || plotWidth <= 0) return;

    const xForNs = (ns: number) => plotLeft + ((ns - zoom.startNs) / span) * plotWidth;

    // Background panel
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(plotLeft, TOP_PADDING, plotWidth, ordered.length * (ROW_HEIGHT + ROW_GAP));

    // Each row
    for (let i = 0; i < ordered.length; i++) {
      const conn = ordered[i];
      const rowY = TOP_PADDING + i * (ROW_HEIGHT + ROW_GAP);

      // Left gutter label
      ctx.fillStyle = topologyColor(conn.topology_id);
      ctx.fillRect(0, rowY, 4, ROW_HEIGHT);
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textBaseline = "middle";
      ctx.fillText(conn.connection_id, 12, rowY + ROW_HEIGHT / 2);

      // Cells
      const { rows, bucketSeconds, firstNs } = rowsForConnection(conn.connection_id);
      const widthNs = bucketSeconds * 1e9;
      for (const r of rows) {
        const cellStart = firstNs + r[0] * widthNs;
        const cellEnd = cellStart + widthNs;
        if (cellEnd < zoom.startNs || cellStart > zoom.endNs) continue;
        const x0 = Math.max(plotLeft, xForNs(cellStart));
        const x1 = Math.min(plotRight, xForNs(cellEnd));
        const w = Math.max(1, x1 - x0);
        ctx.fillStyle = colorFor(r, colorMode, bucketSeconds);
        ctx.fillRect(x0, rowY, w, ROW_HEIGHT);
        if (r[5] === 1) {
          // warmup hatch overlay (semi-transparent diagonal lines)
          ctx.save();
          ctx.globalAlpha = 0.55;
          ctx.fillStyle = "rgba(255,255,255,0.18)";
          ctx.fillRect(x0, rowY, w, ROW_HEIGHT);
          ctx.restore();
        }
      }

      // Gap overlays for this connection
      for (const g of gaps) {
        if (g.connection_id !== conn.connection_id) continue;
        if (g.end_ns < zoom.startNs || g.start_ns > zoom.endNs) continue;
        const x0 = Math.max(plotLeft, xForNs(g.start_ns));
        const x1 = Math.min(plotRight, xForNs(g.end_ns));
        const w = Math.max(2, x1 - x0);
        ctx.save();
        ctx.fillStyle = "rgba(220, 38, 38, 0.55)";
        ctx.fillRect(x0, rowY, w, ROW_HEIGHT);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.6)";
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 0.5, rowY + 0.5, w - 1, ROW_HEIGHT - 1);
        ctx.restore();
      }

      // Transition markers
      for (const t of transitions) {
        if (t.connection_id !== conn.connection_id) continue;
        if (t.at_ns < zoom.startNs || t.at_ns > zoom.endNs) continue;
        const x = xForNs(t.at_ns);
        if (t.kind === "reconnect") {
          ctx.fillStyle = "#000000";
          ctx.fillRect(x - 1, rowY, 2, ROW_HEIGHT);
        } else if (t.kind === "disconnect") {
          ctx.strokeStyle = "#000000";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(x, rowY);
          ctx.lineTo(x, rowY + ROW_HEIGHT);
          ctx.stroke();
          ctx.strokeStyle = "rgba(255,255,255,0.8)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, rowY);
          ctx.lineTo(x, rowY + ROW_HEIGHT);
          ctx.stroke();
        } else if (t.kind === "market_rebind") {
          ctx.fillStyle = "#06b6d4";
          ctx.fillRect(x - 1, rowY, 2, ROW_HEIGHT);
        } else if (t.kind === "error") {
          ctx.fillStyle = "#f97316";
          ctx.fillRect(x - 1, rowY, 2, ROW_HEIGHT);
        }
      }
    }

    // Bottom axis
    drawAxis(ctx, plotLeft, plotRight, height - BOTTOM_AXIS, zoom);

    // Brush overlay
    if (brush && Math.abs(brush.endX - brush.startX) > 2) {
      const x0 = Math.min(brush.startX, brush.endX);
      const x1 = Math.max(brush.startX, brush.endX);
      ctx.save();
      ctx.fillStyle = "rgba(56, 189, 248, 0.18)";
      ctx.fillRect(x0, TOP_PADDING, x1 - x0, ordered.length * (ROW_HEIGHT + ROW_GAP));
      ctx.strokeStyle = "rgba(56, 189, 248, 0.9)";
      ctx.strokeRect(x0 + 0.5, TOP_PADDING + 0.5, x1 - x0 - 1, ordered.length * (ROW_HEIGHT + ROW_GAP) - 1);
      ctx.restore();
    }
  }, [
    ordered,
    rowsForConnection,
    transitions,
    gaps,
    zoom,
    colorMode,
    brush,
    width,
    height,
  ]);

  // Mouse interaction.
  const pixelToNs = useCallback(
    (px: number) => {
      const plotLeft = LEFT_GUTTER;
      const plotWidth = width - RIGHT_PADDING - LEFT_GUTTER;
      const span = zoom.endNs - zoom.startNs;
      const clamped = Math.max(plotLeft, Math.min(width - RIGHT_PADDING, px));
      return zoom.startNs + ((clamped - plotLeft) / plotWidth) * span;
    },
    [width, zoom],
  );

  const cellAtPosition = useCallback(
    (px: number, py: number): { conn: { connection_id: string; topology_id: string }; row?: BucketRow; timeNs: number } | null => {
      if (px < LEFT_GUTTER || px > width - RIGHT_PADDING) return null;
      const idx = Math.floor((py - TOP_PADDING) / (ROW_HEIGHT + ROW_GAP));
      if (idx < 0 || idx >= ordered.length) return null;
      const conn = ordered[idx];
      const ns = pixelToNs(px);
      const { rows, bucketSeconds, firstNs } = rowsForConnection(conn.connection_id);
      const widthNs = bucketSeconds * 1e9;
      const bucketIdx = Math.floor((ns - firstNs) / widthNs);
      const row = rows.find((r) => r[0] === bucketIdx);
      return { conn, row, timeNs: ns };
    },
    [ordered, pixelToNs, rowsForConnection, width],
  );

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < LEFT_GUTTER || px > width - RIGHT_PADDING) return;
    dragStateRef.current = { startX: px, lastX: px };
    setBrush({ startX: px, endX: px });
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const cell = cellAtPosition(px, py);
    if (cell) {
      onHover({
        connectionId: cell.conn.connection_id,
        timeNs: cell.timeNs,
        row: cell.row,
      });
    } else {
      onHover(null);
    }
    if (dragStateRef.current) {
      dragStateRef.current.lastX = px;
      setBrush({ startX: dragStateRef.current.startX, endX: px });
    }
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const drag = dragStateRef.current;
    dragStateRef.current = null;
    setBrush(null);

    if (drag && Math.abs(px - drag.startX) > 4) {
      const a = pixelToNs(Math.min(drag.startX, px));
      const b = pixelToNs(Math.max(drag.startX, px));
      if (b - a >= 1_000_000) {
        // require ≥ 1ms to count as a real zoom (avoid jitter)
        setZoom({ startNs: a, endNs: b });
      }
      return;
    }

    // Treat as a click — open inspector for the bucket under the cursor.
    const cell = cellAtPosition(px, py);
    if (cell && cell.row) {
      const { rows, bucketSeconds, firstNs } = rowsForConnection(cell.conn.connection_id);
      void rows;
      const widthNs = bucketSeconds * 1e9;
      const startNs = firstNs + cell.row[0] * widthNs;
      onSelectCell(cell.conn.connection_id, startNs, startNs + widthNs);
    }
  };

  const handleMouseLeave = () => {
    dragStateRef.current = null;
    setBrush(null);
    onHover(null);
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = (e.currentTarget as HTMLCanvasElement).getBoundingClientRect();
    const px = e.clientX - rect.left;
    const focusNs = pixelToNs(px);
    const span = zoom.endNs - zoom.startNs;
    // Wheel up (negative deltaY) → zoom in.
    const factor = e.deltaY < 0 ? 0.7 : 1.3;
    const newSpan = Math.max(1_000_000, Math.min(span * factor, 365 * 86400 * 1e9));
    const ratio = (focusNs - zoom.startNs) / span;
    const start = focusNs - ratio * newSpan;
    const end = start + newSpan;
    setZoom({ startNs: Math.max(start, 0), endNs: end });
  };

  return (
    <div ref={wrapperRef} className="relative w-full">
      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onWheel={handleWheel}
        className="block w-full cursor-crosshair select-none"
      />
    </div>
  );
}

function drawAxis(
  ctx: CanvasRenderingContext2D,
  plotLeft: number,
  plotRight: number,
  y: number,
  zoom: ZoomRange,
) {
  ctx.fillStyle = "#94a3b8";
  ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textBaseline = "top";
  ctx.textAlign = "left";

  const span = (zoom.endNs - zoom.startNs) / 1e9; // seconds
  const ticks = 8;
  const stepNs = (zoom.endNs - zoom.startNs) / ticks;
  for (let i = 0; i <= ticks; i++) {
    const t = zoom.startNs + i * stepNs;
    const x = plotLeft + (i / ticks) * (plotRight - plotLeft);
    ctx.strokeStyle = "rgba(148, 163, 184, 0.2)";
    ctx.beginPath();
    ctx.moveTo(x, y - 4);
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.textAlign = i === 0 ? "left" : i === ticks ? "right" : "center";
    ctx.fillText(formatTick(t, span), x, y + 4);
  }
}

function formatTick(ns: number, spanSeconds: number): string {
  const d = new Date(ns / 1e6);
  if (spanSeconds < 120) {
    return d.toISOString().slice(11, 19);
  }
  if (spanSeconds < 86400) {
    return d.toISOString().slice(11, 16);
  }
  return d.toISOString().slice(5, 16).replace("T", " ");
}
