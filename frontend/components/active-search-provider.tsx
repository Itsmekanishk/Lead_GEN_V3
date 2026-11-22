"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { Job } from "@/types";

/**
 * App-wide "what is running right now" state.
 *
 * The dashboard no longer navigates away when a search starts, and the nav
 * loader is driven by this provider instead of per-page effects, so progress
 * survives every route change. Polling only happens while a job is non-terminal
 * (no SSE fan-out in the layout, no requests while idle).
 */

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const POLL_MS = 3000;

const STATUS_TEXT: Record<string, string> = {
  queued: "Queued…",
  discovering: "Finding businesses…",
  enriching: "Enriching records…",
  crawling: "Scanning websites…",
  extracting: "Extracting contacts…",
  deduplicating: "Removing duplicates…",
  generating_report: "Building report…",
};

interface ActiveSearchValue {
  activeJobs: Job[];
  activeJob: Job | null;
  /** Call right after POST /search so the loader appears immediately. */
  track: (jobId: string) => void;
  refresh: () => void;
}

const ActiveSearchContext = createContext<ActiveSearchValue>({
  activeJobs: [],
  activeJob: null,
  track: () => undefined,
  refresh: () => undefined,
});

export function useActiveSearch(): ActiveSearchValue {
  return useContext(ActiveSearchContext);
}

/** Human-readable one-liner for the loader (stage detail, else stage name). */
export function activeJobLabel(job: Job): string {
  return job.stage_detail?.trim() || STATUS_TEXT[job.status] || job.status;
}

export function ActiveSearchProvider({ children }: { children: ReactNode }) {
  const [activeJobs, setActiveJobs] = useState<Job[]>([]);
  const [trackedId, setTrackedId] = useState<string | null>(null);
  const aliveRef = useRef(true);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const jobs = await api.get<Job[]>("/search");
      if (!aliveRef.current) return;
      setActiveJobs(jobs.filter((job) => job && !TERMINAL.has(job.status)));
    } catch {
      // Keep the last known state: a transient API error must not blank the chip.
    }
  }, []);

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  const track = useCallback(
    (jobId: string) => {
      setTrackedId(jobId);
      void load();
    },
    [load],
  );

  useEffect(() => {
    aliveRef.current = true;
    void load();
    const onVisibility = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      aliveRef.current = false;
      document.removeEventListener("visibilitychange", onVisibility);
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [load]);

  // Self-scheduling poll: runs only while at least one job is in flight.
  useEffect(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (activeJobs.length === 0) return;
    pollRef.current = setTimeout(() => void load(), POLL_MS);
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [activeJobs, load]);

  const ordered = trackedId
    ? [...activeJobs].sort((a, b) =>
        a.id === trackedId ? -1 : b.id === trackedId ? 1 : 0,
      )
    : activeJobs;

  return (
    <ActiveSearchContext.Provider
      value={{ activeJobs: ordered, activeJob: ordered[0] ?? null, track, refresh }}
    >
      {children}
    </ActiveSearchContext.Provider>
  );
}
