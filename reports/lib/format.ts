export function fmtPercent(value: number, digits = 2): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtPercentPoints(value: number, digits = 2): string {
  // value is already in percentage points (e.g. 97.3641)
  return `${value.toFixed(digits)}%`;
}

export function fmtMs(value: number | undefined, digits = 1): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)}s`;
  return `${value.toFixed(digits)}ms`;
}

export function fmtCount(value: number | undefined): string {
  if (value === undefined || value === null) return "—";
  return value.toLocaleString("en-US");
}

export function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(2)}h`;
}

export function fmtTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function fmtDelta(value: number, digits = 1): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}`;
}
