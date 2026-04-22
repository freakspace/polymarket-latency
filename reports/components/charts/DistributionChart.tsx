"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Summary } from "@/lib/summary-schema";
import { topologyDistribution } from "@/lib/aggregate";
import { topologyColor } from "@/lib/palette";
import { ChartCard } from "./ChartCard";

const GRID = "#e2e8f0";
const AXIS = "#64748b";

type Metric = "freshness" | "arrival";

function fmt(v: number) {
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${v.toFixed(0)}ms`;
}

export function DistributionChart({ summary }: { summary: Summary }) {
  const sizes = useMemo(
    () =>
      Object.keys(summary.topologies)
        .map((k) => Number(k))
        .sort((a, b) => a - b),
    [summary]
  );
  const [metric, setMetric] = useState<Metric>("freshness");
  const [size, setSize] = useState<number>(sizes[0] ?? 1);

  const dist = topologyDistribution(summary, metric, size);
  const data = dist.bins.map((b) => ({
    midpoint: Math.round((b.start + b.end) / 2),
    count: b.count,
  }));

  return (
    <ChartCard
      title="Distribution explorer"
      subtitle="Histogram rebuilt from the raw freshness_histogram_ms / arrival_delta_histogram_ms blocks on each topology."
      actions={
        <div className="flex items-center gap-2">
          <MetricToggle metric={metric} setMetric={setMetric} />
          <SizeToggle size={size} sizes={sizes} setSize={setSize} />
        </div>
      }
    >
      <div style={{ height: 280 }}>
        <ResponsiveContainer>
          <BarChart
            data={data}
            margin={{ top: 8, right: 8, bottom: 4, left: -8 }}
          >
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="midpoint"
              stroke={AXIS}
              tickLine={false}
              axisLine={{ stroke: GRID }}
              fontSize={11}
              tickFormatter={fmt}
            />
            <YAxis
              stroke={AXIS}
              tickLine={false}
              axisLine={false}
              fontSize={11}
              width={56}
              tickFormatter={(v: number) => v.toLocaleString("en-US")}
            />
            <Tooltip
              cursor={{ fill: "rgba(37, 99, 235, 0.06)" }}
              contentStyle={{
                background: "#ffffff",
                border: "1px solid #cbd5e1",
                borderRadius: 8,
                fontSize: 12,
                boxShadow: "0 4px 16px rgba(15,23,42,0.08)",
              }}
              labelStyle={{ color: "#0f172a", fontWeight: 600 }}
              itemStyle={{ color: "#0f172a" }}
              labelFormatter={(v: number) => `≈ ${fmt(v)}`}
              formatter={(v: number) => v.toLocaleString("en-US")}
            />
            {dist.distribution?.p50 != null && (
              <ReferenceLine
                x={dist.distribution.p50}
                stroke="#059669"
                strokeDasharray="4 3"
                label={{ value: "p50", fill: "#059669", fontSize: 10 }}
              />
            )}
            {dist.distribution?.p95 != null && (
              <ReferenceLine
                x={dist.distribution.p95}
                stroke="#b45309"
                strokeDasharray="4 3"
                label={{ value: "p95", fill: "#b45309", fontSize: 10 }}
              />
            )}
            <Bar
              dataKey="count"
              fill={dist.color}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {data.length === 0 && (
        <div className="mt-2 text-xs text-text-subtle">
          No histogram available for this selection.
        </div>
      )}
    </ChartCard>
  );
}

function MetricToggle({
  metric,
  setMetric,
}: {
  metric: Metric;
  setMetric: (m: Metric) => void;
}) {
  return (
    <div className="flex rounded-md border border-border bg-bg-base p-0.5 text-xs">
      {(["freshness", "arrival"] as Metric[]).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => setMetric(m)}
          className={
            "rounded px-2.5 py-1 font-medium transition-colors " +
            (metric === m
              ? "bg-bg-elevated text-text"
              : "text-text-subtle hover:text-text")
          }
        >
          {m === "freshness" ? "Freshness" : "Arrival"}
        </button>
      ))}
    </div>
  );
}

function SizeToggle({
  size,
  sizes,
  setSize,
}: {
  size: number;
  sizes: number[];
  setSize: (s: number) => void;
}) {
  return (
    <div className="flex rounded-md border border-border bg-bg-base p-0.5 text-xs">
      {sizes.map((s) => {
        const active = size === s;
        return (
          <button
            key={s}
            type="button"
            onClick={() => setSize(s)}
            className={
              "rounded px-2 py-1 font-mono font-medium transition-colors " +
              (active ? "bg-bg-elevated" : "text-text-subtle hover:text-text")
            }
            style={active ? { color: topologyColor(s) } : undefined}
          >
            {s}
          </button>
        );
      })}
    </div>
  );
}
