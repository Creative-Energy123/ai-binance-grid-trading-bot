import type { ReactNode } from "react";
import { pnlClass } from "../lib/format";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-700/60 bg-panel/80 p-4 shadow-lg ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-200">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "pnl";
  }) {
  const numeric = Number(String(value).replace(/[^0-9.-]/g, ""));
  const toneClass = tone === "pnl" && !Number.isNaN(numeric) ? pnlClass(numeric) : "text-slate-100";
  return (
    <div className="rounded-lg border border-slate-700/50 bg-ink/50 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`font-mono text-lg ${toneClass}`}>{value}</div>
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

const BADGE_TONES: Record<string, string> = {
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  warn: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  bad: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  info: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  muted: "bg-slate-500/10 text-slate-300 border-slate-500/30",
};

export function Badge({ tone = "muted", children }: { tone?: keyof typeof BADGE_TONES | string; children: ReactNode }) {
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-[11px] ${BADGE_TONES[tone] ?? BADGE_TONES.muted}`}>
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  tone = "default",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger" | "warn";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const tones: Record<string, string> = {
    default: "border-slate-600 bg-slate-800/70 hover:bg-slate-700 text-slate-200",
    primary: "border-accent/60 bg-accent/20 hover:bg-accent/30 text-emerald-200",
    danger: "border-rose-500/60 bg-rose-500/15 hover:bg-rose-500/25 text-rose-200",
    warn: "border-amber-500/60 bg-amber-500/15 hover:bg-amber-500/25 text-amber-200",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-xs">
        <thead>
          <tr className="border-b border-slate-700/60 text-[11px] uppercase tracking-wide text-slate-400">
            {head.map((h) => (
              <th key={h} className="whitespace-nowrap px-2 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/70 font-mono text-slate-200">{children}</tbody>
      </table>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-2 py-6 text-center text-xs text-slate-500">{children}</p>;
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{message}</p>
  );
}
