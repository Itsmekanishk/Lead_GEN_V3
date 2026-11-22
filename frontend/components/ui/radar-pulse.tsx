"use client";

import { cn } from "@/lib/utils";

interface RadarPulseProps {
  /** Current phase label streamed over SSE, e.g. "Scanning https://domain.com/contact-us" */
  phase: string;
  active?: boolean;
  className?: string;
}

/**
 * Live pipeline indicator.
 *
 * Deliberately CSS-only: the previous version wrapped the changing label in
 * `<AnimatePresence mode="wait">` keyed on the phase. During crawling the phase
 * changes on every SSE event, which kept mounting/unmounting keyed children and
 * could throw inside the animation layer and blank the page. A single element
 * with an opacity transition looks the same and cannot churn.
 */
export function RadarPulse({ phase, active = true, className }: RadarPulseProps) {
  const label = (phase ?? "").toString().trim();

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border ll-border bg-white/[0.03] px-4 py-3",
        className,
      )}
    >
      <span className="relative flex size-2.5 shrink-0">
        {active && (
          <span className="absolute inset-0 animate-ping rounded-full bg-ll-cyan/70" aria-hidden />
        )}
        <span
          className={cn(
            "relative size-2.5 rounded-full transition-colors",
            active ? "bg-ll-cyan" : "bg-slate-600",
          )}
        />
      </span>
      <span
        className="truncate font-mono text-xs text-slate-300 transition-opacity duration-200"
        title={label}
        aria-live="polite"
      >
        {label}
      </span>
    </div>
  );
}
