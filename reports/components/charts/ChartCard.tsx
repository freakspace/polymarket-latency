import type { ReactNode } from "react";
import { Card } from "../primitives/Card";

export function ChartCard({
  title,
  subtitle,
  children,
  actions,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-3">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-semibold">{title}</h3>
          {subtitle ? (
            <p className="mt-1 max-w-[56ch] text-xs text-text-subtle">
              {subtitle}
            </p>
          ) : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </header>
      <div>{children}</div>
    </Card>
  );
}
