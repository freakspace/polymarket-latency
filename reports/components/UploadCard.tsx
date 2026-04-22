"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { parseSummary } from "@/lib/summary-schema";

const UPLOAD_KEY = "ws-benchmark-uploaded-summary";

export function UploadCard() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setDragging] = useState(false);

  const ingest = useCallback(
    async (file: File) => {
      setError(null);
      try {
        const text = await file.text();
        const parsed = parseSummary(JSON.parse(text));
        if (typeof window !== "undefined") {
          sessionStorage.setItem(
            UPLOAD_KEY,
            JSON.stringify({
              summary: parsed,
              filename: file.name,
              loadedAt: Date.now(),
            })
          );
        }
        router.push("/report/uploaded");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(`Could not parse summary: ${message.slice(0, 240)}`);
      }
    },
    [router]
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) ingest(file);
      }}
      className={
        "card card-pad flex flex-col items-center justify-center gap-2 border-dashed text-center transition-colors " +
        (isDragging
          ? "border-accent bg-accent-soft"
          : "border-border hover:border-border-strong")
      }
    >
      <div className="eyebrow text-accent">Or load an arbitrary file</div>
      <div className="text-sm text-text">
        Drop a{" "}
        <span className="font-mono text-text-muted">summary.json</span> here
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) ingest(file);
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="mt-1 rounded-md border border-border bg-bg-elevated px-3 py-1.5 text-xs font-medium text-text-muted transition-colors hover:text-text"
      >
        or choose a file
      </button>
      {error && <div className="mt-2 text-xs text-bad">{error}</div>}
    </div>
  );
}
