import type { Summary } from "@/lib/summary-schema";
import { marketRotationTimeline, type SegmentCell } from "@/lib/aggregate";
import { Card } from "./primitives/Card";
import { SEGMENT_COLORS } from "@/lib/palette";
import { fmtDuration, fmtMs } from "@/lib/format";

function fmtTimeUtc(iso: string): string {
  const d = new Date(iso);
  const hh = d.getUTCHours().toString().padStart(2, "0");
  const mm = d.getUTCMinutes().toString().padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

function fmtTimeRange(startIso: string, endIso: string): string {
  const s = new Date(startIso);
  const e = new Date(endIso);
  const hh = (d: Date) =>
    `${d.getUTCHours().toString().padStart(2, "0")}:${d
      .getUTCMinutes()
      .toString()
      .padStart(2, "0")}`;
  return `${hh(s)}–${hh(e)}`;
}

function segColor(seg: SegmentCell): string {
  if (seg.hasTopo1Gap) return SEGMENT_COLORS.topo1;
  if (seg.hasSocketStall) return SEGMENT_COLORS.stall;
  return SEGMENT_COLORS.normal;
}

function segmentIndexLabel(index: number): string {
  return String(index + 1).padStart(3, "0");
}

export function MarketRotationTimeline({ summary }: { summary: Summary }) {
  const timeline = marketRotationTimeline(summary);
  if (!timeline) return null;
  const { segments, markerIndices } = timeline;

  const flagged = segments.filter((s) => s.hasSocketStall);
  const midpointIndex = Math.floor(segments.length / 2);
  const midpointIso = segments[midpointIndex].startedAt;

  return (
    <section className="mb-8">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow text-segment-stall">
            <span className="mr-2 inline-block h-[2px] w-6 translate-y-[-3px] bg-segment-stall align-middle" />
            Ch · The run
          </div>
          <h2 className="mt-2 font-serif text-[32px] font-bold leading-tight tracking-tight text-text lg:text-[40px]">
            Market rotation timeline.
          </h2>
        </div>
        <div className="font-mono text-xs text-text-subtle">
          SERIES{" "}
          <span className="font-semibold text-accent">
            {timeline.seriesId}
          </span>
          {" · "}
          <span className="text-text">{timeline.marketFamily}</span>
          {" · "}
          <span className="text-text">{segments.length} SEGMENTS</span>
          {" · "}
          <span className="text-text">
            {timeline.totalDurationSeconds.toLocaleString("en-US")}S (
            {fmtDuration(timeline.totalDurationSeconds)})
          </span>
        </div>
      </header>

      <Card className="relative !p-6">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="text-[17px] font-semibold text-text">
              {segments.length} market rotation
              {segments.length === 1 ? "" : "s"} ·{" "}
              {timeline.totalDurationSeconds.toLocaleString("en-US")} scored
              seconds
            </h3>
            <p className="mt-1.5 max-w-[80ch] text-[13px] leading-relaxed text-text-muted">
              Each bar is one 5-minute{" "}
              <span className="font-mono">{timeline.marketFamily.toLowerCase()}</span>{" "}
              binary market. Amber bars mark segments that contained
              socket-level stalls large enough to register in per-connection gap
              runs — the topology-level effect (i.e. what the deduplicated
              pipeline saw) was mostly absorbed by healthy peers.
            </p>
          </div>
          <Legend />
        </div>

        <SegmentStrip segments={segments} markerIndices={markerIndices} />

        <div className="mt-3 flex items-baseline justify-between font-mono text-xxs text-text-subtle">
          <span>
            <span className="font-semibold text-text">
              {fmtTimeUtc(timeline.startedAtIso)}
            </span>
            {" · run start"}
          </span>
          <span>
            <span className="font-semibold text-text">
              {fmtTimeUtc(midpointIso)}
            </span>
            {" · midpoint"}
          </span>
          <span>
            <span className="font-semibold text-text">
              {fmtTimeUtc(timeline.endedAtIso)}
            </span>
            {" · run end"}
          </span>
        </div>
      </Card>

      {flagged.length > 0 && (
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {flagged.map((seg) => (
            <FlaggedSegmentCard key={seg.segmentId} seg={seg} />
          ))}
        </div>
      )}
    </section>
  );
}

function Legend() {
  const items: Array<{
    color: string;
    label: string;
    sub: string;
  }> = [
    { color: SEGMENT_COLORS.normal, label: "normal", sub: "" },
    { color: SEGMENT_COLORS.stall, label: "socket", sub: "stall hotspot" },
    { color: SEGMENT_COLORS.topo1, label: "topo-1", sub: "single-socket gap" },
  ];
  return (
    <div className="flex gap-4 text-xxs">
      {items.map((it) => (
        <div key={it.label} className="flex items-start gap-1.5">
          <span
            className="mt-[2px] inline-block h-3 w-3 rounded-[3px]"
            style={{ background: it.color }}
          />
          <div>
            <div className="font-semibold text-text">{it.label}</div>
            {it.sub && (
              <div className="font-mono text-text-subtle">{it.sub}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function SegmentStrip({
  segments,
  markerIndices,
}: {
  segments: SegmentCell[];
  markerIndices: number[];
}) {
  const markerSet = new Set(markerIndices);
  const flagged = segments.map((s) => s.hasSocketStall);

  return (
    <div className="relative">
      <div className="flex h-[56px] items-stretch gap-[2px]">
        {segments.map((seg) => (
          <div
            key={seg.segmentId}
            title={`${seg.segmentId} · ${seg.marketSlug} · ${fmtTimeRange(seg.startedAt, seg.endedAt)}${seg.hasSocketStall ? ` · worst socket gap ${fmtMs(seg.worstGapMs)}` : ""}`}
            className="relative flex-1 overflow-hidden rounded-[3px] transition-[filter] hover:brightness-110"
            style={{ background: segColor(seg) }}
          >
            {seg.hasSocketStall && (
              <span
                aria-hidden
                className="absolute inset-x-0 bottom-0 h-[3px]"
                style={{
                  background:
                    "linear-gradient(to right, rgba(0,0,0,0.2), rgba(0,0,0,0.05))",
                }}
              />
            )}
          </div>
        ))}
      </div>
      <div className="pointer-events-none mt-1.5 grid auto-cols-fr grid-flow-col text-xxs">
        {segments.map((seg) => {
          const shouldLabel = markerSet.has(seg.index);
          return (
            <div
              key={seg.segmentId}
              className={
                "text-center font-mono " +
                (flagged[seg.index]
                  ? "font-semibold text-segment-stall"
                  : "text-text-subtle")
              }
            >
              {shouldLabel ? segmentIndexLabel(seg.index) : ""}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FlaggedSegmentCard({ seg }: { seg: SegmentCell }) {
  const color = seg.hasTopo1Gap ? SEGMENT_COLORS.topo1 : SEGMENT_COLORS.stall;
  const tone = seg.hasTopo1Gap ? "topo-1 gap" : "socket stall";
  return (
    <div
      className="rounded-xl border bg-bg-surface p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
      style={{ borderColor: color }}
    >
      <div
        className="mb-1 font-mono text-xxs font-semibold uppercase tracking-wider"
        style={{ color }}
      >
        SEG_{segmentIndexLabel(seg.index)} · {fmtTimeRange(seg.startedAt, seg.endedAt)}
      </div>
      <div className="font-mono text-xxs text-text-subtle">
        {seg.marketSlug}
      </div>
      <div className="mt-2 font-mono text-[11px] text-text-muted">
        {tone} · worst {fmtMs(seg.worstGapMs)} across {seg.gapHits.length}{" "}
        socket{seg.gapHits.length === 1 ? "" : "s"}
      </div>
      <ul className="mt-2 space-y-0.5 font-mono text-[11px]">
        {seg.gapHits.map((g) => (
          <li key={g.connectionId} className="flex justify-between gap-2">
            <span className="truncate text-text-muted">{g.connectionId}</span>
            <span className="text-text">{fmtMs(g.durationMs)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
