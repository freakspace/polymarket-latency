"use client";

import { useEffect, useState } from "react";

type RawEvent = {
  event_key?: string;
  event_type?: string;
  connection_id?: string;
  topology_id?: string;
  received_at_ns?: number;
  received_at_iso?: string;
  venue_timestamp_ns?: number | null;
  venue_timestamp_iso?: string | null;
  in_warmup?: boolean;
  phase_kind?: string;
  segment_id?: string;
  switch_reason?: string;
};

type Selection = {
  connectionId: string;
  startNs: number;
  endNs: number;
};

export function SocketTimelineDrillPanel({
  timestamp,
  selection,
  onClose,
}: {
  timestamp: string;
  selection: Selection | null;
  onClose: () => void;
}) {
  const [events, setEvents] = useState<RawEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ truncated: boolean; total: number } | null>(null);

  useEffect(() => {
    if (!selection) {
      setEvents([]);
      setMeta(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const u = new URL(
      `/api/events/${encodeURIComponent(timestamp)}`,
      window.location.origin,
    );
    u.searchParams.set("connection_id", selection.connectionId);
    u.searchParams.set("start_ns", String(selection.startNs));
    u.searchParams.set("end_ns", String(selection.endNs));
    u.searchParams.set("limit", "500");

    fetch(u.toString())
      .then(async (r) => {
        if (!r.ok) throw new Error(`api ${r.status}`);
        return r.json();
      })
      .then((j) => {
        if (cancelled) return;
        setEvents(j.events ?? []);
        setMeta({ truncated: !!j.truncated, total: j.events?.length ?? 0 });
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selection, timestamp]);

  if (!selection) return null;

  const startIso = new Date(selection.startNs / 1e6).toISOString();
  const endIso = new Date(selection.endNs / 1e6).toISOString();
  const windowSeconds = (selection.endNs - selection.startNs) / 1e9;

  return (
    <aside className="fixed right-0 top-0 z-30 flex h-full w-[480px] flex-col border-l border-border bg-bg-surface shadow-xl">
      <header className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="min-w-0">
          <div className="eyebrow text-accent">Drill-down</div>
          <h3 className="mt-1 truncate font-mono text-sm font-semibold text-text">
            {selection.connectionId}
          </h3>
          <p className="mt-1 font-mono text-xxs text-text-subtle">
            {startIso.slice(11, 23)} – {endIso.slice(11, 23)} ({windowSeconds.toFixed(0)}s)
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="font-mono text-xs text-text-subtle transition-colors hover:text-text"
        >
          ✕ close
        </button>
      </header>

      <div className="flex-1 overflow-auto p-4">
        {loading && <div className="text-xs text-text-subtle">loading…</div>}
        {error && (
          <div className="rounded-md border border-red-700 bg-red-900/20 p-3 text-xs text-red-300">
            {error}
          </div>
        )}
        {!loading && !error && events.length === 0 && (
          <div className="text-xs text-text-subtle">
            no raw events for this connection in this window
          </div>
        )}
        {events.length > 0 && (
          <>
            <div className="mb-2 font-mono text-xxs text-text-subtle">
              {events.length} event{events.length === 1 ? "" : "s"}
              {meta?.truncated ? " (truncated to limit)" : ""}
            </div>
            <ul className="space-y-1 font-mono text-[11px]">
              {events.map((e, i) => (
                <li
                  key={i}
                  className="rounded-md border border-border bg-bg-base/50 p-2"
                >
                  <div className="flex justify-between gap-2">
                    <span className="text-text">{e.event_type}</span>
                    <span className="text-text-subtle">
                      {e.received_at_iso?.slice(11, 23) ?? "—"}
                    </span>
                  </div>
                  <div className="mt-1 truncate text-text-subtle">
                    {e.event_key}
                  </div>
                  {e.venue_timestamp_ns && e.received_at_ns && (
                    <div className="mt-1 text-text-muted">
                      freshness: {((e.received_at_ns - e.venue_timestamp_ns) / 1e6).toFixed(1)}ms
                      {e.in_warmup ? " · warmup" : ""}
                      {e.phase_kind ? ` · ${e.phase_kind}` : ""}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </aside>
  );
}
