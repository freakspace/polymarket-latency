import Link from "next/link";
import { notFound } from "next/navigation";
import { loadRun } from "@/lib/recordings";
import { ReportView } from "@/components/ReportView";
import { UploadedReport } from "./UploadedReport";

export const dynamic = "force-dynamic";

export default async function ReportPage({
  params,
}: {
  params: Promise<{ timestamp: string }>;
}) {
  const { timestamp } = await params;

  if (timestamp === "uploaded") {
    return (
      <>
        <BackLink />
        <UploadedReport />
      </>
    );
  }

  const run = await loadRun(timestamp);
  if (!run) notFound();

  return (
    <>
      <BackLink />
      <ReportView
        summary={run.summary}
        timestamp={run.timestamp}
        hasTimeline={run.hasTimeline}
      />
    </>
  );
}

function BackLink() {
  return (
    <div className="mb-4">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-xs text-text-subtle transition-colors hover:text-text"
      >
        ← All runs
      </Link>
    </div>
  );
}
