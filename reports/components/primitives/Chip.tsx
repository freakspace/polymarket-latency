import type { ReactNode } from "react";

type Tone = "good" | "warn" | "bad" | "accent" | "neutral";

const TONES: Record<Tone, string> = {
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  accent: "bg-accent-soft text-accent",
  neutral: "bg-bg-muted text-text-muted",
};

export function Chip({
  tone = "neutral",
  children,
}: {
  tone?: Tone;
  children: ReactNode;
}) {
  return <span className={`chip ${TONES[tone]}`}>{children}</span>;
}
