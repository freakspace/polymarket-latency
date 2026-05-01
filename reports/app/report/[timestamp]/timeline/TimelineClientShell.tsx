"use client";

import { useState } from "react";
import { SocketTimeline } from "@/components/SocketTimeline";
import { SocketTimelineDrillPanel } from "@/components/SocketTimelineDrillPanel";
import type { TimelineBundle } from "@/lib/timeline";

type Selection = { connectionId: string; startNs: number; endNs: number };

export function TimelineClientShell({
  timestamp,
  bundle,
}: {
  timestamp: string;
  bundle: TimelineBundle;
}) {
  const [selection, setSelection] = useState<Selection | null>(null);
  return (
    <>
      <SocketTimeline
        timestamp={timestamp}
        bundle={bundle}
        onSelectWindow={(connectionId, startNs, endNs) =>
          setSelection({ connectionId, startNs, endNs })
        }
      />
      <SocketTimelineDrillPanel
        timestamp={timestamp}
        selection={selection}
        onClose={() => setSelection(null)}
      />
    </>
  );
}
