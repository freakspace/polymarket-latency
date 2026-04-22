"use client";

import { useEffect, useState } from "react";
import { parseSummary, type Summary } from "@/lib/summary-schema";
import { ReportView } from "@/components/ReportView";

const UPLOAD_KEY = "ws-benchmark-uploaded-summary";

type Loaded = {
  summary: Summary;
  filename: string;
  loadedAt: number;
};

export function UploadedReport() {
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ok"; data: Loaded } | { kind: "empty" } | { kind: "error"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(UPLOAD_KEY);
      if (!raw) {
        setState({ kind: "empty" });
        return;
      }
      const parsed = JSON.parse(raw) as { summary: unknown; filename: string; loadedAt: number };
      const summary = parseSummary(parsed.summary);
      setState({
        kind: "ok",
        data: {
          summary,
          filename: parsed.filename,
          loadedAt: parsed.loadedAt,
        },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({ kind: "error", message });
    }
  }, []);

  if (state.kind === "loading") {
    return (
      <div className="card card-pad text-sm text-text-muted">Loading…</div>
    );
  }
  if (state.kind === "empty") {
    return (
      <div className="card card-pad text-sm text-text-muted">
        No uploaded summary in this tab. Go back to the run list and drop a file.
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="card card-pad text-sm text-bad">
        Could not load uploaded summary: {state.message}
      </div>
    );
  }
  return (
    <>
      <div className="mb-2 font-mono text-xs text-text-subtle">
        uploaded: {state.data.filename}
      </div>
      <ReportView summary={state.data.summary} />
    </>
  );
}
