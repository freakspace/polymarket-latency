import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`card card-pad ${className}`}>{children}</div>;
}

export function SectionHeader({
  eyebrow,
  title,
  subtitle,
  trailing,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div>
        {eyebrow ? <div className="eyebrow mb-1">{eyebrow}</div> : null}
        <h2 className="text-lg font-semibold text-text">{title}</h2>
        {subtitle ? (
          <p className="mt-1 max-w-[60ch] text-sm text-text-muted">{subtitle}</p>
        ) : null}
      </div>
      {trailing ? <div className="shrink-0">{trailing}</div> : null}
    </div>
  );
}
