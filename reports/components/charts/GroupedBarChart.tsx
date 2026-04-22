"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartPayload } from "@/lib/aggregate";

const GRID = "#e2e8f0";
const AXIS = "#64748b";
const TOOLTIP_BG = "#ffffff";
const TOOLTIP_BORDER = "#cbd5e1";
const TOOLTIP_LABEL = "#0f172a";
const TOOLTIP_TEXT = "#0f172a";

function formatValue(value: number, kind: ChartPayload["valueKind"]): string {
  if (kind === "percent") return `${value.toFixed(2)}%`;
  if (kind === "ms") {
    if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)}s`;
    return `${value.toFixed(1)}ms`;
  }
  return value.toLocaleString("en-US");
}

type Row = Record<string, number | string>;

export function GroupedBarChart({
  payload,
  height = 280,
}: {
  payload: ChartPayload;
  height?: number;
}) {
  const data: Row[] = payload.categories.map((cat, i) => {
    const row: Row = { category: cat };
    for (const s of payload.series) {
      row[s.label] = s.values[i] ?? 0;
    }
    return row;
  });

  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer>
        <BarChart
          data={data}
          margin={{ top: 8, right: 8, bottom: 4, left: -8 }}
          barCategoryGap="22%"
        >
          <CartesianGrid
            stroke={GRID}
            strokeDasharray="0"
            vertical={false}
          />
          <XAxis
            dataKey="category"
            stroke={AXIS}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            fontSize={12}
          />
          <YAxis
            stroke={AXIS}
            tickLine={false}
            axisLine={false}
            fontSize={11}
            tickFormatter={(v: number) => formatValue(v, payload.valueKind)}
            width={60}
          />
          <Tooltip
            cursor={{ fill: "rgba(37, 99, 235, 0.06)" }}
            contentStyle={{
              background: TOOLTIP_BG,
              border: `1px solid ${TOOLTIP_BORDER}`,
              borderRadius: 8,
              fontSize: 12,
              boxShadow: "0 4px 16px rgba(15,23,42,0.08)",
            }}
            labelStyle={{ color: TOOLTIP_LABEL, fontWeight: 600 }}
            itemStyle={{ color: TOOLTIP_TEXT }}
            formatter={(v: number) => formatValue(v, payload.valueKind)}
          />
          <Legend
            wrapperStyle={{
              color: "#475569",
              fontSize: 12,
              paddingTop: 8,
            }}
            iconType="circle"
            iconSize={8}
          />
          {payload.series.map((s) => (
            <Bar
              key={s.label}
              dataKey={s.label}
              fill={s.color}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
