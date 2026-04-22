import Link from "next/link";
import { notFound } from "next/navigation";
import { loadOrderBurst } from "@/lib/order-burst";
import { OrderBurstReport } from "@/components/OrderBurstReport";

export const dynamic = "force-dynamic";

export default async function OrderBurstReportPage({
  params,
}: {
  params: Promise<{ timestamp: string }>;
}) {
  const { timestamp } = await params;
  const run = await loadOrderBurst(timestamp);
  if (!run) notFound();

  return (
    <>
      <div className="mb-4">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-xs text-text-subtle transition-colors hover:text-text"
        >
          ← All runs
        </Link>
      </div>
      <OrderBurstReport run={run} />
    </>
  );
}
