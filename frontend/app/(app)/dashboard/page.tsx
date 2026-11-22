"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Building2, CheckCircle2, Globe, Mail, Phone, Search, Settings2, SlidersHorizontal,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Job, SearchInterpretResponse } from "@/types";
import { useJobStream } from "@/hooks/useJobStream";
import { useActiveSearch } from "@/components/active-search-provider";
import {
  AnimatedCounter, Badge, Button, Card, ConfidenceBadge, Input, ProviderBadge,
  RadarPulse, Slider, Switch,
} from "@/components/ui";
import { Progress, useToast } from "@/components/legacy-ui";

const EXAMPLES = [
  "Hospitals in Porwal Road Pune",
  "Societies in Porwal Road Pune",
  "Schools in Viman Nagar Pune",
  "Dental clinics in Kharadi Pune",
  "Hotels near Hinjewadi Pune",
];

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  discovering: "Searching businesses…",
  enriching: "Normalizing records…",
  crawling: "Scanning websites…",
  extracting: "Extracting contacts…",
  deduplicating: "Removing duplicates…",
  generating_report: "Generating report…",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

// Pipeline stages exactly as emitted by the worker (backend-verified order).
const STAGES = ["queued", "discovering", "enriching", "crawling", "extracting", "deduplicating"];
const STAGE_LABEL: Record<string, string> = {
  queued: "Job queued",
  discovering: "Business discovery",
  enriching: "Record enrichment",
  crawling: "Website crawling",
  extracting: "Contact extraction",
  deduplicating: "Deduplication",
};

const PROVIDER_OPTIONS: { value: string; label: string }[] = [
  { value: "auto", label: "Auto (Configured Default)" },
  { value: "geoapify", label: "Geoapify Places (High Accuracy)" },
  { value: "nominatim", label: "OpenStreetMap (Free & Open Source)" },
  { value: "google", label: "Google Places (API Key Required)" },
  { value: "serper", label: "Serper (API Key Required)" },
  { value: "mock", label: "Demo data (mock)" },
];

export default function DashboardPage() {
  const router = useRouter();
  const { push } = useToast();
  const { track } = useActiveSearch();

  const [mode, setMode] = useState<"free" | "structured">("free");
  const [freeText, setFreeText] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [maxResults, setMaxResults] = useState(50);
  const [radius, setRadius] = useState(5000); // meters, backend allows 100–50,000
  const [radiusOn, setRadiusOn] = useState(false);
  const [country, setCountry] = useState("in");
  const [language, setLanguage] = useState("en");
  // "auto" = server default + fallback chain; otherwise pin a provider.
  const [provider, setProvider] = useState("auto");
  const [respectRobots, setRespectRobots] = useState(true);
  const [excludeDisposable, setExcludeDisposable] = useState(false);

  const [interpretation, setInterpretation] = useState<SearchInterpretResponse | null>(null);
  const [interpreting, setInterpreting] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const { job } = useJobStream(jobId);
  const running = job && !["completed", "failed", "cancelled"].includes(job.status);

  // Debounced natural-language interpret (300 ms) — mirrors the live parse the
  // backend will run, without creating a job.
  const preview = useCallback(async () => {
    if (mode !== "free" || freeText.trim().length < 4) {
      setInterpretation(null);
      return;
    }
    setInterpreting(true);
    try {
      setInterpretation(
        await api.post<SearchInterpretResponse>("/search/interpret", {
          free_text: freeText, max_results: maxResults, country, language,
        }),
      );
    } catch {
      setInterpretation(null); // parse preview is best-effort; submit still validates
    } finally {
      setInterpreting(false);
    }
  }, [mode, freeText, maxResults, country, language]);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (mode !== "free") return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => void preview(), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [mode, preview]);

  async function startSearch() {
    setSubmitError(null);
    const hasInput = mode === "free"
      ? freeText.trim().length > 0
      : category.trim().length > 0 && location.trim().length > 0;
    if (!hasInput) {
      setSubmitError(mode === "free"
        ? "Type what to search for first — e.g. \"Hospitals in Porwal Road Pune\"."
        : "Enter both an industry and a location.");
      return;
    }
    setSubmitting(true);
    try {
      const body = {
        ...(mode === "free"
          ? { free_text: freeText }
          : { category, location }),
        max_results: maxResults,
        country,
        language,
        radius_m: radiusOn ? radius : undefined,
        provider: provider === "auto" ? undefined : provider,
      };
      const res = await api.post<{ job_id: string; status: string }>("/search", body);
      setJobId(res.job_id);
      // Stay here: the card below is the live view and the nav loader keeps
      // running while the user moves around. Navigating away immediately was
      // what hid the scraping progress on this page.
      track(res.job_id);
      setInterpretation(null);
      push("Search started — live progress is in the nav bar.", "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Search failed";
      setSubmitError(msg);
      push(msg, "error");
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setJobId(null);
    setInterpretation(null);
  }

  const runningStage = job ? STAGES.indexOf(job.status) : -1;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Find Businesses</h1>
        <p className="text-sm text-muted-foreground">
          Describe what you&apos;re looking for — we parse, discover, enrich and
          de-duplicate contacts automatically.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* search card */}
        <Card className="lg:col-span-2">
          <div className="mb-4 flex rounded-lg bg-muted p-1 text-sm font-medium">
            {(["free", "structured"] as const).map((m) => (
              <button
                key={m}
                className={`flex-1 rounded-md px-3 py-1.5 transition ${
                  mode === m ? "bg-card shadow-sm" : "text-muted-foreground"
                }`}
                onClick={() => setMode(m)}
              >
                {m === "free" ? "Natural language" : "Structured"}
              </button>
            ))}
          </div>

          {mode === "free" ? (
            <div className="mb-4">
              <label className="label" htmlFor="q">Search query</label>
              <Input
                id="q"
                icon={<Search className="h-4 w-4" />}
                placeholder="Hospitals in Porwal Road Pune"
                value={freeText}
                onChange={(e) => setFreeText(e.target.value)}
              />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    className="badge bg-muted text-muted-foreground hover:bg-border"
                    onClick={() => setFreeText(ex)}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mb-4 grid gap-3">
              <div>
                <label className="label" htmlFor="cat">Industry</label>
                <Input
                  id="cat" placeholder="Hospital" value={category}
                  onChange={(e) => setCategory(e.target.value)}
                />
              </div>
              <div>
                <label className="label" htmlFor="loc">Location</label>
                <Input
                  id="loc" placeholder="Porwal Road, Pune" value={location}
                  onChange={(e) => setLocation(e.target.value)}
                />
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="max">Maximum results</label>
              <Input
                id="max" type="number" min={1} max={200} value={maxResults}
                onChange={(e) => setMaxResults(Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label" htmlFor="country">Country (ISO-2)</label>
              <Input
                id="country" maxLength={2} value={country}
                onChange={(e) => setCountry(e.target.value)}
              />
            </div>
          </div>

          <button
            type="button"
            className="mt-3 flex w-full items-center justify-between rounded-lg border ll-border bg-white/[0.03] px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-white/[0.06]"
            onClick={() => setAdvancedOpen((v) => !v)}
            aria-expanded={advancedOpen}
          >
            <span className="flex items-center gap-2">
              <SlidersHorizontal className="h-4 w-4" />
              Advanced controls
            </span>
            <Settings2 className="h-4 w-4 text-slate-500" />
          </button>
          {advancedOpen && (
            <Card variant="flat" className="mt-2 space-y-4 p-4">
              <Slider
                label="Search radius"
                min={500}
                max={50000}
                step={500}
                value={radius}
                onChange={setRadius}
                formatValue={(v) => `${v.toLocaleString()} m`}
              />
              <Switch
                id="radius-on"
                checked={radiusOn}
                onChange={setRadiusOn}
                label="Use radius filter"
                description="Applies the radius above to the discovery query."
              />
              <div>
                <label className="label" htmlFor="provider">Data source</label>
                <select
                  id="provider"
                  className="input w-full"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  {PROVIDER_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {PROVIDER_OPTIONS.filter((o) => o.value !== "auto").map((o) => (
                    <ProviderBadge key={o.value} provider={o.value} />
                  ))}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  If the chosen source hits a limit or goes down, the next
                  configured source is used automatically and the results page
                  will say so.
                </p>
              </div>
              <div>
                <label className="label" htmlFor="lang">Language</label>
                <Input
                  id="lang" maxLength={8} value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                />
              </div>
              <Switch
                id="robots"
                checked={respectRobots}
                onChange={setRespectRobots}
                label="Respect robots.txt"
                description="Website scans always honor robots.txt; this records your preference."
              />
              <Switch
                id="disposable"
                checked={excludeDisposable}
                onChange={setExcludeDisposable}
                label="Filter disposable emails"
                description="Drop leads whose email is on a disposable/free mail host (Disify)."
              />
            </Card>
          )}

          <div className="mt-4 flex gap-2">
            <Button variant="secondary" onClick={() => void preview()} className="flex-1">
              Preview parse
              {interpreting && <span className="sr-only">(checking…)</span>}
            </Button>
            <Button
              glow
              className="flex-1"
              disabled={submitting || Boolean(running)}
              loading={submitting}
              onClick={startSearch}
            >
              <Search className="h-4 w-4" />
              {running ? "Running…" : "Search Businesses"}
            </Button>
          </div>
          {submitError && (
            <p role="alert" className="mt-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
              {submitError}
            </p>
          )}
          {jobId && (
            <button
              className="mt-3 w-full text-center text-xs text-muted-foreground hover:underline"
              onClick={reset}
            >
              Start a new search
            </button>
          )}
        </Card>

        {/* right column: interpretation or progress */}
        <div className="space-y-4 lg:col-span-3">
          {interpretation && !jobId && (
            <Card>
              <h2 className="mb-3 font-semibold">Interpreted Search</h2>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {interpretation.interpreted.category && (
                  <Badge tone="ll-indigo">Category: {interpretation.interpreted.category}</Badge>
                )}
                {interpretation.interpreted.location && (
                  <Badge tone="ll-cyan">Location: {interpretation.interpreted.location}</Badge>
                )}
                <ConfidenceBadge
                  level={interpretation.interpreted.confidence.toUpperCase()}
                />
              </div>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Estimated results</dt>
                  <dd className="font-medium">up to {interpretation.estimated_max_results}</dd>
                </div>
              </dl>
              {interpretation.interpreted.notes?.length > 0 && (
                <ul className="mt-3 list-inside list-disc text-xs text-muted-foreground">
                  {interpretation.interpreted.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
              <Button glow className="mt-4 w-full" onClick={startSearch} loading={submitting}>
                Start Search
              </Button>
            </Card>
          )}

          {jobId && (
            <Card>
              {job ? (
                <>
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="font-semibold">{STATUS_LABEL[job.status] ?? job.status}</h2>
                    <StatusChip status={job.status} />
                  </div>
                  <Progress value={job.progress} />
                  <p className="mt-2 text-sm text-muted-foreground">{job.stage_detail || "Working…"}</p>
                  <RadarPulse
                    className="mt-3"
                    active={!["completed", "failed", "cancelled"].includes(job.status)}
                    phase={job.stage_detail || STATUS_LABEL[job.status] || job.status}
                  />

                  <ol className="mt-4 space-y-1.5 text-sm" aria-label="Pipeline stages">
                    {STAGES.map((stage, i) => {
                      const done = job.status === "completed" || (runningStage > i && runningStage !== -1);
                      const active = job.status === stage;
                      const failedStage = job.status === "failed" && runningStage === i;
                      return (
                        <li key={stage} className="flex items-center gap-2">
                          <span
                            aria-hidden
                            className={
                              done ? "text-ll-confidence-high" : active ? "text-ll-indigo" : failedStage ? "text-ll-confidence-low" : "text-muted-foreground"
                            }
                          >
                            {done ? "✓" : failedStage ? "✗" : active ? "●" : "○"}
                          </span>
                          <span className={done || active ? "font-medium" : "text-muted-foreground"}>
                            {STAGE_LABEL[stage]}
                          </span>
                        </li>
                      );
                    })}
                  </ol>

                  <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Stat icon={<Building2 className="h-4 w-4" />} label="Found" value={job.progress_stats?.total ?? 0} />
                    <Stat icon={<Globe className="h-4 w-4" />} label="Websites" value={job.progress_stats?.websites_found ?? 0} />
                    <Stat icon={<Mail className="h-4 w-4" />} label="Emails" value={job.progress_stats?.emails_found ?? 0} />
                    <Stat icon={<Phone className="h-4 w-4" />} label="Phones" value={job.progress_stats?.phones_found ?? 0} />
                    <Stat icon={<CheckCircle2 className="h-4 w-4" />} label="Processed" value={job.progress_stats?.completed ?? 0} />
                    <Stat icon={<Settings2 className="h-4 w-4" />} label="Failed scans" value={job.progress_stats?.failed ?? 0} />
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button glow onClick={() => router.push(`/results/${job.id}`)}>
                      {job.status === "completed" ? "View Results" : "Open Live Progress & Results"}
                    </Button>
                    {job.status === "completed" && (
                      <Button variant="secondary" onClick={() => router.push(`/results/${job.id}?tab=export`)}>
                        Downloads
                      </Button>
                    )}
                  </div>
                  {job.status === "failed" && (
                    <p className="mt-3 rounded-md bg-ll-confidence-low-soft p-3 text-sm text-ll-confidence-low">
                      {job.error_message || "The search failed. Please try again."}
                    </p>
                  )}
                </>
              ) : (
                <div className="flex flex-col items-center justify-center gap-3 py-8">
                  <span className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                  <p className="text-sm font-medium text-muted-foreground">Connecting to search pipeline…</p>
                  <Button variant="secondary" size="sm" onClick={() => router.push(`/results/${jobId}`)}>
                    Open Results Page
                  </Button>
                </div>
              )}
            </Card>
          )}

          {!jobId && (
            <Card>
              <h2 className="mb-2 font-semibold">How it works</h2>
              <ol className="list-inside list-decimal space-y-1.5 text-sm text-muted-foreground">
                <li>We parse your query into industry + location (shown live as you type).</li>
                <li>Discovery runs against the configured business data provider (official APIs only).</li>
                <li>Each business&apos;s own public website is politely scanned (robots.txt respected) for contact pages.</li>
                <li>Emails and phones are extracted with source + confidence, then de-duplicated.</li>
                <li>Export a branded PDF, CSV or Excel report.</li>
              </ol>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === "completed" ? "success"
      : status === "failed" ? "danger"
        : status === "cancelled" ? "neutral"
          : "ll-cyan";
  return <Badge tone={tone}>{STATUS_LABEL[status] ?? status}</Badge>;
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="rounded-lg border ll-border p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <AnimatedCounter className="mt-1 block text-xl font-bold" value={value} />
    </div>
  );
}
