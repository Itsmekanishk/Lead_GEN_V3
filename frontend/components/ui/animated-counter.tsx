"use client";

import * as React from "react";
import { animate, motion, useMotionValue, useTransform } from "framer-motion";

interface AnimatedCounterProps {
  value: number;
  className?: string;
  /** e.g. "ms", "%" — appended after the number without a space. */
  suffix?: string;
}

/**
 * Counts smoothly from its previous value to `value` whenever it changes —
 * used for live SSE-driven stats so numbers never jump-cut or reflow the layout.
 *
 * Non-finite input (undefined stats from a partial payload) is coerced to 0:
 * feeding NaN into a framer-motion value poisons the animation loop.
 */
export function AnimatedCounter({ value, className, suffix }: AnimatedCounterProps) {
  const safeValue = Number.isFinite(value) ? Number(value) : 0;
  const motionValue = useMotionValue(safeValue);
  const rounded = useTransform(motionValue, (v) => Math.round(Number.isFinite(v) ? v : 0).toLocaleString());

  React.useEffect(() => {
    const controls = animate(motionValue, safeValue, { duration: 0.6, ease: "easeOut" });
    return controls.stop;
  }, [safeValue, motionValue]);

  return (
    <span className={className}>
      <motion.span className="tabular-nums">{rounded}</motion.span>
      {suffix}
    </span>
  );
}
