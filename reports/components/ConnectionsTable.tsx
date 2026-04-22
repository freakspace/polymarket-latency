"use client";

import { useMemo, useState } from "react";
import type { Summary } from "@/lib/summary-schema";
import { connectionRows, type ConnectionRow } from "@/lib/aggregate";
import { topologyColor } from "@/lib/palette";
import { Card, SectionHeader } from "./primitives/Card";
import { fmtMs, fmtPercent } from "@/lib/format";

type SortKey = keyof Pick<
  ConnectionRow,
  | "connectionId"
  | "topologySize"
  | "coverageRate"
  | "firstSeenWinRate"
  | "arrivalP95"
  | "freshnessP95"
  | "reconnects"
  | "disconnects"
  | "duplicateObservations"
  | "longestSilenceSeconds"
>;

const COLUMNS: Array<{
  key: SortKey;
  label: string;
  numeric: boolean;
  format: (row: ConnectionRow) => string;
}> = [
  {
    key: "connectionId",
    label: "Connection",
    numeric: false,
    format: (r) => r.connectionId,
  },
  {
    key: "topologySize",
    label: "Topology",
    numeric: true,
    format: (r) => `${r.topologySize} ws`,
  },
  {
    key: "coverageRate",
    label: "Coverage",
    numeric: true,
    format: (r) => fmtPercent(r.coverageRate),
  },
  {
    key: "firstSeenWinRate",
    label: "First Seen",
    numeric: true,
    format: (r) => fmtPercent(r.firstSeenWinRate),
  },
  {
    key: "arrivalP95",
    label: "Arrival p95",
    numeric: true,
    format: (r) => fmtMs(r.arrivalP95),
  },
  {
    key: "freshnessP95",
    label: "Freshness p95",
    numeric: true,
    format: (r) => fmtMs(r.freshnessP95),
  },
  {
    key: "reconnects",
    label: "Reconnects",
    numeric: true,
    format: (r) => r.reconnects.toString(),
  },
  {
    key: "disconnects",
    label: "Disconnects",
    numeric: true,
    format: (r) => r.disconnects.toString(),
  },
  {
    key: "duplicateObservations",
    label: "Dup Obs",
    numeric: true,
    format: (r) => r.duplicateObservations.toLocaleString("en-US"),
  },
  {
    key: "longestSilenceSeconds",
    label: "Longest Silence",
    numeric: true,
    format: (r) => `${r.longestSilenceSeconds.toFixed(2)}s`,
  },
];

export function ConnectionsTable({ summary }: { summary: Summary }) {
  const [sortKey, setSortKey] = useState<SortKey>("freshnessP95");
  const [desc, setDesc] = useState<boolean>(true);
  const rawRows = useMemo(() => connectionRows(summary), [summary]);
  const rows = useMemo(() => {
    const sorted = [...rawRows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "number" && typeof bv === "number") return av - bv;
      return String(av).localeCompare(String(bv));
    });
    return desc ? sorted.reverse() : sorted;
  }, [rawRows, sortKey, desc]);

  const onHeaderClick = (key: SortKey) => {
    if (key === sortKey) setDesc((d) => !d);
    else {
      setSortKey(key);
      setDesc(true);
    }
  };

  return (
    <section className="mb-8">
      <SectionHeader
        eyebrow="Per-connection"
        title="Connection detail"
        subtitle="Sortable. Click any column header. Per-connection metrics are rebuilt from raw per-socket records in summary.json."
      />
      <Card className="!p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead>
              <tr className="border-b border-border">
                {COLUMNS.map((col) => {
                  const active = col.key === sortKey;
                  return (
                    <th
                      key={col.key}
                      onClick={() => onHeaderClick(col.key)}
                      className={
                        "cursor-pointer select-none p-3 text-xxs font-semibold uppercase tracking-wider transition-colors hover:text-text " +
                        (active ? "text-accent" : "text-text-subtle") +
                        " " +
                        (col.numeric ? "text-right" : "text-left") +
                        (col.key === "connectionId" ? " pl-5" : "")
                      }
                    >
                      {col.label}
                      {active ? (desc ? " ↓" : " ↑") : ""}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.connectionId}
                  className="border-b border-border last:border-0 hover:bg-bg-muted"
                >
                  {COLUMNS.map((col) => {
                    const isIdCell = col.key === "connectionId";
                    const isSizeCell = col.key === "topologySize";
                    return (
                      <td
                        key={col.key}
                        className={
                          "p-3 font-mono text-[13px] " +
                          (col.numeric ? "text-right" : "text-left") +
                          (isIdCell ? " pl-5 font-semibold text-text" : " text-text")
                        }
                        style={
                          isSizeCell
                            ? { color: topologyColor(r.topologySize) }
                            : undefined
                        }
                      >
                        {col.format(r)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </section>
  );
}
