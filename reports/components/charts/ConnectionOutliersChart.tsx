"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Summary } from "@/lib/summary-schema";
import { connectionOutliers } from "@/lib/aggregate";
import { ChartCard } from "./ChartCard";
import { METRIC_COLORS } from "@/lib/palette";

const GRID = "#e2e8f0";
const AXIS = "#64748b";

function fmt(v: number) {
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${v.toFixed(1)}ms`;
}

export function ConnectionOutliersChart({ summary }: { summary: Summary }) {
  const rows = connectionOutliers(summary, { limit: 12 });
  const data = rows.map((r) => ({
    label: r.label,
    freshness: r.freshnessP95,
    arrival: r.arrivalP95,
    color: r.color,
    connectionId: r.connectionId,
  }));
  return (
    <ChartCard
      title="Connection Outliers"
      subtitle="Worst 12 connections by freshness p95, with arrival p95 for tie-breaks. Colour marks the topology each connection belongs to."
    >
      <div style={{ height: Math.max(280, data.length * 26 + 60) }}>
        <ResponsiveContainer>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 8, right: 16, bottom: 4, left: 16 }}
          >
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis
              type="number"
              stroke={AXIS}
              tickLine={false}
              axisLine={{ stroke: GRID }}
              fontSize={11}
              tickFormatter={fmt}
            />
            <YAxis
              type="category"
              dataKey="label"
              stroke={AXIS}
              tickLine={false}
              axisLine={false}
              fontSize={12}
              width={56}
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
              formatter={(v: number) => fmt(v)}
            />
            <Legend
              wrapperStyle={{ color: "#475569", fontSize: 12, paddingTop: 8 }}
              iconType="circle"
              iconSize={8}
            />
            <Bar
              dataKey="freshness"
              name="Freshness p95"
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
            >
              {data.map((d, i) => (
                <Cell key={i} fill={d.color} />
              ))}
            </Bar>
            <Bar
              dataKey="arrival"
              name="Arrival p95"
              fill={METRIC_COLORS.arrivalP95}
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
              opacity={0.6}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}
