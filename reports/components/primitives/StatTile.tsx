type Tone = "good" | "warn" | "bad" | "accent" | "neutral";

const VALUE_TONES: Record<Tone, string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  accent: "text-accent",
  neutral: "text-text",
};

export function StatTile({
  label,
  value,
  detail,
  tone = "neutral",
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: Tone;
}) {
  return (
    <div className="card card-pad flex flex-col gap-1">
      <div className="eyebrow">{label}</div>
      <div
        className={`font-mono text-[28px] font-semibold leading-none tabular ${VALUE_TONES[tone]}`}
      >
        {value}
      </div>
      {detail ? (
        <div className="text-xs text-text-subtle">{detail}</div>
      ) : null}
    </div>
  );
}
