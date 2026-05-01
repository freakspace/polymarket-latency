import Link from "next/link";
import type { Route } from "next";
import { loadTimelineBundle } from "@/lib/timeline-server";
import { TimelineClientShell } from "./TimelineClientShell";

export const dynamic = "force-dynamic";

export default async function TimelinePage({
  params,
}: {
  params: Promise<{ timestamp: string }>;
}) {
  const { timestamp } = await params;
  const bundle = await loadTimelineBundle(timestamp);
  if (!bundle) {
    return (
      <main>
        <BackToReport timestamp={timestamp} />
        <div className="rounded-xl border border-border bg-bg-surface p-6">
          <h1 className="text-lg font-semibold">No timeline artifacts</h1>
          <p className="mt-2 text-sm text-text-muted">
            This run does not have <code className="font-mono">timeline/index.json</code>.
            Generate it with:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-md bg-bg-base p-3 font-mono text-xs">
            make timeline SUMMARY=recordings/ws-bench/{timestamp}/summary.json
          </pre>
          <p className="mt-3 text-xs text-text-subtle">
            (Requires the run to have been recorded with{" "}
            <code className="font-mono">--write-event-log --write-connection-log</code>.)
          </p>
        </div>
      </main>
    );
  }

  return (
    <main>
      <BackToReport timestamp={timestamp} />
      <TimelineClientShell timestamp={timestamp} bundle={bundle} />
    </main>
  );
}

function BackToReport({ timestamp }: { timestamp: string }) {
  return (
    <div className="mb-4">
      <Link
        href={`/report/${timestamp}` as Route}
        className="inline-flex items-center gap-1 text-xs text-text-subtle transition-colors hover:text-text"
      >
        ← Back to report
      </Link>
    </div>
  );
}
