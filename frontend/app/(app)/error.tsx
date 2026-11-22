"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

/**
 * Route-level error boundary for the authenticated app shell.
 *
 * Without this file any render error inside a page unmounts the whole tree and
 * the browser shows a blank page. Now a failure keeps the header/nav usable and
 * offers a retry, so live progress and history stay reachable.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Route render failed:", error);
  }, [error]);

  return (
    <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-red-500/30 bg-red-500/[0.06] p-6">
      <div className="flex items-center gap-2 text-red-200">
        <AlertTriangle className="size-5" aria-hidden />
        <h1 className="text-base font-semibold">Something went wrong on this page</h1>
      </div>
      <p className="text-sm text-slate-300">
        The search itself keeps running on the server. Retry this view, or open it
        again from the live indicator in the navigation bar.
      </p>
      {error?.message && (
        <p className="rounded-md bg-black/30 px-3 py-2 font-mono text-xs text-slate-400">
          {error.message}
        </p>
      )}
      <button
        onClick={reset}
        className="inline-flex items-center gap-1.5 rounded-lg bg-ll-indigo px-3 py-2 text-sm font-medium text-white hover:bg-ll-indigo/90"
      >
        <RefreshCw className="size-4" aria-hidden />
        Try again
      </button>
    </div>
  );
}
