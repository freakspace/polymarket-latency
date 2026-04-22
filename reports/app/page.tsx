import { listRuns, recordingsRoot } from "@/lib/recordings";
import { RunCard } from "@/components/RunCard";
import { UploadCard } from "@/components/UploadCard";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const runs = await listRuns();

  return (
    <main>
      <header className="mb-8 border-b border-border pb-8">
        <div className="eyebrow text-accent">Polymarket CLOB WS Benchmark</div>
        <h1 className="mt-2 text-[32px] font-bold leading-tight tracking-tight">
          Benchmark runs
        </h1>
        <p className="mt-3 max-w-[72ch] text-sm text-text-muted">
          Every run written under{" "}
          <span className="font-mono text-text">recordings/ws-bench/</span> is
          listed here. Open one to see the full topology-scaling report — charts
          and tables are rebuilt from raw per-socket metrics in{" "}
          <span className="font-mono text-text">summary.json</span>.
        </p>
        <p className="mt-2 font-mono text-xxs text-text-subtle">
          root: {recordingsRoot()}
        </p>
      </header>

      {runs.length === 0 ? (
        <div className="card card-pad mb-6 text-sm text-text-muted">
          No runs found. Run{" "}
          <span className="font-mono text-text">make benchmark</span> from the
          repo root, then reload this page.
        </div>
      ) : (
        <div className="mb-6 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {runs.map((run) => (
            <RunCard key={run.timestamp} run={run} />
          ))}
        </div>
      )}

      <UploadCard />
    </main>
  );
}
