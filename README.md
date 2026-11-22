<div align="center">

# LocalLead Intelligence

**Find businesses by industry and location, enrich them with verified public contact data, and export branded reports — with provenance for every email and phone.**

`Hospitals in Porwal Road, Pune` → 67 businesses · 61 phones · 52 websites · 39 emails (32 high confidence) → PDF / CSV / Excel

[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-000000.svg)](https://nextjs.org/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ed.svg)](https://docs.docker.com/compose/)

</div>

---

## Table of contents

- [What this is](#what-this-is)
- [Why it is built this way](#why-it-is-built-this-way)
- [Feature set](#feature-set)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Quick start (5 minutes, no API keys)](#quick-start-5-minutes-no-api-keys)
- [Local development without Docker](#local-development-without-docker)
- [Configuration](#configuration)
- [Data providers](#data-providers)
- [API surface](#api-surface)
- [Live progress in the UI](#live-progress-in-the-ui)
- [Exports](#exports)
- [Testing](#testing)
- [Deployment](#deployment)
- [Operations](#operations)
- [Security posture](#security-posture)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [The standalone `leadgen/` engine](#the-standalone-leadgen-engine)
- [Documentation map](#documentation-map)
- [Contributing](#contributing)
- [License](#license)

---

## What this is

LocalLead Intelligence is a self-hosted lead-generation platform for small sales and research teams. A user describes what they want in plain language — *"Dental clinics in Kharadi Pune"* — and the platform:

1. **Parses** the request into a category and a location (with a confidence score).
2. **Discovers** matching businesses through an official data provider.
3. **Enriches** each business by politely crawling its own public website for contact details.
4. **Extracts** emails and phones with provenance, source URL and a confidence tier.
5. **De-duplicates** near-identical records across the result set.
6. **Streams** every stage to the browser live, then **exports** PDF, CSV or Excel.

It ships with a zero-key mock provider so the entire product can be run and demonstrated offline, and it defaults to OpenStreetMap (Nominatim) for genuinely free, keyless real data.

**Who it is for:** a team that needs its own lead pipeline with data it can audit — every contact row points at the page it came from — rather than a black-box scraper.

---

## Why it is built this way

These are product constraints, not implementation details. They explain most of the code:

| Principle | What it means in practice |
|---|---|
| **Official sources only** | Business discovery uses the Google Places API (New), Geoapify, Serper, or OpenStreetMap — never scraped search-engine results or marketplaces. |
| **Provenance over volume** | Every email/phone stores `source`, `source_url`, `confidence` and `discovered_at`. Nothing is guessed or generated. |
| **Polite by default** | robots.txt honoured, honest User-Agent, ≥2 s between requests to the same domain, page and byte caps, per-business failure isolation. |
| **Honest reporting** | If you ask for 100 leads and only 45 exist, the UI says exactly that — it never pads a result set. |
| **Never a blank screen** | Provider outages, dead websites and render failures degrade into visible, recoverable states (banners, retry cards), never a white page. |
| **Secrets stay out of git** | Configuration is environment-only, validated at boot, and redacted from logs. |

---

## Feature set

| Area | Capabilities |
|---|---|
| **Discovery** | Natural-language or structured queries, radius/country/language filters, per-search provider choice, automatic fallback chain |
| **Enrichment** | Parallel website crawl (homepage → contact/about), email extraction (mailto + text), phone normalization to E.164, disposable-email detection |
| **Quality** | Confidence tiers (HIGH/MEDIUM/LOW), duplicate resolution by normalized website/phone plus fuzzy name+address, per-contact source URLs |
| **Live UX** | SSE progress stream with polling fallback, stage-by-stage pipeline view, a persistent header loader that survives navigation |
| **Data** | Filterable, sortable results table, per-business detail with crawl pages, summary counters |
| **Exports** | Branded PDF, CSV and Excel with formula-injection neutralization and ownership-checked, UUID-named downloads |
| **Accounts** | Argon2 passwords, JWT access + rotating refresh tokens with reuse detection, ADMIN/USER roles, password reset |
| **Admin** | All users' jobs, user management, provider keys, provider usage and estimated cost, audit log |
| **Ops** | `/api/health` covering DB, Redis, worker and provider; structured JSON logs with secret redaction; Alembic migrations; Docker healthchecks |

---

## Architecture

```
                        ┌──────────────────────────── Browser ───────────────────────────┐
                        │  Next.js 14 (App Router, TS, Tailwind)                          │
                        │  dashboard · results · history · settings · admin                │
                        └───────────────┬──────────────────────────────┬──────────────────┘
                                        │ /backend/* (proxy)           │ SSE /api/search/{id}/stream
                                        ▼                              ▼
                              ┌───────────────────┐          ┌────────────────────┐
                              │  nginx (TLS/SSE)  │─────────▶│   FastAPI (uvicorn)│
                              └───────────────────┘          └─────┬────────┬─────┘
                                                                   │        │
                                                     ┌─────────────▼──┐  ┌──▼──────────┐
                                                     │ Redis (pub/sub │  │ PostgreSQL  │
                                                     │ + Celery queue)│  │  (SQLAlchemy│
                                                     └───────┬────────┘  │   + Alembic)│
                                                             │           └──▲──────────┘
                                                             ▼              │
                                                     ┌──────────────────┐   │
                                                     │  Celery worker    │───┘
                                                     │  (pipeline)       │
                                                     └───┬──────────┬────┘
                                                         ▼          ▼
                                              ┌────────────────┐  ┌────────────────────┐
                                              │ Business       │  │ Business websites  │
                                              │ provider API   │  │ (polite crawler)   │
                                              └────────────────┘  └────────────────────┘
```

### Search pipeline

| # | Status | What happens |
|---|---|---|
| 1 | `discovering` | Query parsed → provider search with selective field masks |
| 2 | `enriching` | Names/URLs/phones normalized, `businesses` rows persisted (unique per job + place id) |
| 3 | `crawling` | Parallel website scans (default 4), homepage → contact/about, `MAX_PAGES_PER_DOMAIN` cap, robots.txt + per-domain rate limit |
| 4 | `extracting` | Email/phone extraction with confidence, `business_contacts` + `crawl_pages` written |
| 5 | `deduplicating` | Strong identifiers merge; name+address fuzzy matching; canonical row kept |
| 6 | `completed` | Counters published; exports unlocked |

Terminal states are `completed`, `failed` and `cancelled`. Per-business failures (timeout, 403/404/429/5xx, DNS, SSL, malformed HTML) are recorded and isolated — one dead website never sinks a job.

### Progress delivery

Workers write state to PostgreSQL (durable) and publish to Redis `locallead:job_events` (live). `GET /api/search/{id}/stream` replays the last known state, subscribes for updates, and falls back to keep-alives plus database polling, so a missed event never strands the UI. The frontend consumes SSE in `useJobStream`, with polling as a fallback.

---

## Repository layout

```
.
├── backend/                  FastAPI service
│   ├── app/
│   │   ├── api/              REST routers: auth, search, business, exports, admin, health
│   │   ├── core/             config, security, startup checks, rate limiting, logging
│   │   ├── crawlers/         polite crawler, robots.txt, per-domain limiter
│   │   ├── exports/          PDF (ReportLab), CSV, Excel (openpyxl) writers
│   │   ├── models/           SQLAlchemy models
│   │   ├── providers/        provider interface + mock / google / serper / geoapify / nominatim
│   │   ├── services/         query parser, dedup resolver, pipeline orchestration
│   │   ├── utils/            SSRF guard, normalizers
│   │   └── workers/          Celery app, pipeline stages, progress pub/sub, in-process dispatch
│   ├── alembic/              migrations
│   ├── scripts/              live E2E, cancellation and load harnesses
│   └── tests/                14 test modules (unit, API, crawler, SSRF, exports, pipeline)
├── frontend/                 Next.js 14 + TypeScript + Tailwind
│   ├── app/                  routes: (app)/dashboard, (app)/results/[jobId], (app)/history,
│   │                         (app)/settings, (app)/admin, register, forgot-password,
│   │                         error.tsx, global-error.tsx
│   ├── components/           UI primitives + active-search provider (header loader)
│   ├── hooks/                useJobStream (SSE + polling fallback)
│   └── lib/api.ts            typed API client: token refresh, friendly error mapping
├── infrastructure/           backend/worker/frontend Dockerfiles, nginx (TLS, SSE-safe)
├── cloudflare/               Workers + Containers deployment ($5/mo, whole app)
├── deploy/                   $0 split: Cloudflare Pages frontend + Render API
├── leadgen/                  standalone open-source lead-gen engine (CLI, MIT)
├── docs/                     architecture, api, deployment, security, testing, crawler, troubleshooting
├── docker-compose.yml        postgres · redis · api · worker · frontend · nginx
├── .env.example              every configuration variable, annotated
├── requirements.txt          standalone engine dependencies
├── PROJECT_EXPLANATION.md    deep dive: concepts, journeys, data model
└── run.sh                    run API + worker + web locally without Docker
```

---

## Quick start (5 minutes, no API keys)

**Prerequisites:** Docker Engine 24+ with Compose v2. Nothing else.

```bash
git clone https://github.com/Itsmekanishk/Lead_GEN_V3.git
cd Lead_GEN_V3
cp .env.example .env          # defaults run entirely in mock mode
docker compose up --build
```

| Service | URL |
|---|---|
| Web app (via nginx) | http://localhost:8080 |
| Frontend (direct) | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/api/docs |
| Health check | http://localhost:8000/api/health |

Open the app, **Create account** — the first registered user becomes **ADMIN** automatically. Run a search; mock mode needs no keys and completes in seconds.

Verify the stack is healthy:

```bash
curl -s http://localhost:8000/api/health | jq
# {"status":"ok","app_env":"production",
#  "checks":{"database":"ok","redis":"ok","workers":"ok","provider":"ok"}}
```

> `workers: "no_workers"` is expected on single-process deployments (Render, Cloudflare Containers): the API then runs each search in-process instead of waiting for Celery.

---

## Local development without Docker

**Backend** — http://localhost:8000, auto-reload

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL=sqlite:///./data/dev.db
export BUSINESS_PROVIDER=mock
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**Frontend** — http://localhost:3000, proxies `/backend/*` to the API

```bash
cd frontend
npm install
npm run dev
```

**Everything at once** (API + Celery worker + web UI, Ctrl+C stops all three):

```bash
./run.sh                 # requires backend/.venv and frontend/node_modules
API_PORT=8001 ./run.sh   # the proxy follows the port automatically
```

Environment requirements: **Python 3.11+** (3.12 recommended), **Node 18.17+** (20 LTS recommended), **PostgreSQL 14+** and **Redis 7+** for production parity — SQLite and the mock provider are enough for local work.

---

## Configuration

All configuration is environment-only. Copy the annotated template and edit it:

```bash
cp .env.example .env      # gitignored; .env.example is the committed reference
```

**The variables that matter most**

| Variable | Required | Notes |
|---|---|---|
| `SECRET_KEY` | **Yes** | Signs JWTs. Generate with `openssl rand -hex 32`. Production **refuses to boot** on the placeholder. |
| `DATABASE_URL` | **Yes (prod)** | `postgresql+psycopg2://user:pass@host:5432/dbname` (SQLite supported for local dev) |
| `REDIS_URL` | **Yes (prod)** | `redis://host:6379/0` — progress pub/sub and Celery broker |
| `BUSINESS_PROVIDER` | Yes | `mock` · `nominatim` · `geoapify` · `google` · `serper` |
| `CORS_ORIGINS` | **Yes (prod)** | Comma-separated, exact-match browser origins |
| `FRONTEND_ORIGIN` | Recommended | Always allow-listed in addition to `CORS_ORIGINS` |
| `ENCRYPTION_KEY` | Optional | Fernet key for provider keys stored via the admin UI |
| `SMTP_*` | Optional | Only needed for password-reset email delivery |

Operational knobs (all documented in `.env.example`): `MAX_RESULTS`, `MAX_PAGES_PER_DOMAIN`, `CRAWL_TIMEOUT`, `CRAWL_CONCURRENCY`, `CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC`, `RATE_LIMIT_SEARCH_PER_MIN`, `RATE_LIMIT_API_PER_MIN`, `ENABLE_WEBSITE_CRAWLING`, `SSRF_PROTECTION`, `RESPECT_ROBOTS_TXT`, `PROVIDER_FALLBACK_CHAIN`, `PROVIDER_STRICT_CONFIG`.

**Startup validation.** With `APP_ENV=production`, missing keys, a placeholder `SECRET_KEY`, or a provider without its key make the server **refuse to start**, naming the variable. In development it boots with a warning banner instead. A search started against an unconfigured provider fails cleanly with a user-safe message.

---

## Data providers

| Provider | Key | Notes |
|---|---|---|
| `mock` | none | Deterministic, offline. Fault-injection scenarios (`timeout`, `empty`) for demos and tests. |
| `nominatim` | none | Businesses discovered from OpenStreetMap (amenity/shop/office tags). Free, keyless, MIT-licensed data; throttled to ≤1 req/s with an honest User-Agent per the OSM usage policy. |
| `geoapify` | `GEOAPIFY_API_KEY` | Free tier of ~3,000 requests/day. |
| `google` | `GOOGLE_MAPS_API_KEY` | Places API (New) with selective field masks (13 fields, never `*`) and per-call cost tracking. |
| `serper` | `SERPER_API_KEY` | Alternative discovery source. |

Several providers can be configured at once: users then pick the source per search (Auto / Google / Serper / Geoapify / OSM), and `PROVIDER_FALLBACK_CHAIN` decides what serves the request if the chosen one is rate-limited or down.

```
PROVIDER_FALLBACK_CHAIN=auto            # default provider, then any other configured one
PROVIDER_FALLBACK_CHAIN=google,serper   # explicit order
PROVIDER_FALLBACK_CHAIN=nominatim       # degrade to free OSM data last
PROVIDER_FALLBACK_CHAIN=                # fallback disabled
```

Two more free services are used, both keyless and both optional: **Nominatim geocoding** (`ENABLE_NOMINATIM_GEOCODE=true`) to turn *"Porwal Road, Pune"* into a lat/lng centre, and **Disify** (`ENABLE_DISIFY_CHECKS=true`) to downgrade throwaway email domains to LOW confidence.

---

## API surface

Interactive OpenAPI docs at `/api/docs` (Swagger) and `/api/redoc`. Full reference: [`docs/api.md`](docs/api.md).

| Group | Endpoints |
|---|---|
| **Auth** | `POST /api/auth/register` · `login` · `refresh` · `logout` · `forgot-password` · `reset-password` · `GET /api/auth/me` |
| **Search** | `POST /api/search/interpret` (parse preview) · `POST /api/search` (202 + `job_id`) · `GET /api/search` · `GET /api/search/{id}` · `GET /api/search/{id}/stream` (SSE) · `GET /api/search/{id}/results` (filter + paginate) · `GET /api/search/{id}/summary` · `POST /api/search/{id}/cancel` |
| **Business** | `GET /api/business/{id}` — record with `contacts[]` and `crawl_pages[]` |
| **Exports** | `POST /api/search/{id}/export/{pdf,csv,xlsx}` → `{filename}` · `GET /api/search/{id}/export/{kind}?filename=` |
| **Admin** | `GET /api/admin/jobs` · `users` · `PATCH /api/admin/users/{id}` · `GET /api/admin/usage` · `GET/PUT /api/admin/providers` · `GET /api/admin/audit-logs` |
| **Health** | `GET /api/health` |

`Authorization: Bearer <access_token>` on every protected route. Errors are always `{"detail": "human-readable message"}` — `401`, `403`, `404` (also used for cross-user access, to prevent enumeration), `409` (export before completion), `422`, `429` (+`Retry-After`), `500` (logged server-side, generic to the client).

Job statuses: `queued → discovering → enriching → crawling → extracting → deduplicating → completed | failed | cancelled`.

---

## Live progress in the UI

- The **results page** streams SSE and renders stage-by-stage progress, counters and the data-quality banner.
- The **dashboard** stays on screen after `POST /search` and shows the live progress card — no forced redirect, so the search you just started is the thing you watch.
- A **header loader** (`ActiveSearchProvider`) shows the current stage and percentage for any non-terminal job on every page: it discovers running jobs on mount, polls every 3 s *only* while one exists, and follows `visibilitychange`. It is the first row of the mobile menu.
- Render failures land on `error.tsx` / `global-error.tsx` — a "Something went wrong — Retry" card, never a blank page.

---

## Exports

Three formats, generated server-side, ownership-checked on download:

- **PDF** — branded report with summary, per-business contact cards and confidence tiers (ReportLab; HTML escaped).
- **CSV / XLSX** — one row per business; cells beginning with `=`, `+`, `-` or `@` are prefixed with `'` to neutralize spreadsheet formula injection.

Files are UUID-named and served only to their owner (or an admin). On single-container deployments the storage directory is ephemeral — see [Known limitations](#known-limitations).

---

## Testing

```bash
cd backend
./.venv/bin/python -m pytest -q                 # unit, API, crawler, SSRF, exports, pipeline

DATABASE_URL=postgresql+psycopg2://locallead:locallead@localhost:5432/locallead \
REDIS_URL=redis://localhost:6379/0 \
./.venv/bin/python scripts/live_e2e.py          # real uvicorn + Celery + Postgres + Redis

./.venv/bin/python scripts/test_cancel.py       # cancellation lifecycle
./.venv/bin/python scripts/load_test.py         # concurrent job throughput

cd ../frontend && npm run build                 # production build + type checking
```

What the suites cover: query parsing across example queries, contact normalization, dedup scoring, crawler behaviour against local HTTP fixtures, the SSRF protection matrix (IPv4/IPv6 ranges, DNS resolution, redirect re-validation), provider failure modes (timeout, empty, auth error, malformed payload), API auth/RBAC/rate limits, search lifecycle, and PDF/CSV/XLSX generation including Unicode and formula injection.

Conventions: tests never call third-party sites (local HTTP fixtures + mock provider, with `SSRF_ALLOWED_HOSTS=127.0.0.1` so SSRF protection stays **on**), no test is skipped or weakened to make a suite pass, and anything needing real credentials is reported as unverified rather than faked. Details: [`docs/TESTING.md`](docs/TESTING.md).

---

## Deployment

| Option | Cost | Shape | Guide |
|---|---|---|---|
| **Docker Compose on a VPS** | VPS cost | Full stack behind nginx with TLS: postgres, redis, api, worker, frontend, nginx | [`docs/deployment.md`](docs/deployment.md) · [`docs/HOSTINGER_DEPLOYMENT.md`](docs/HOSTINGER_DEPLOYMENT.md) |
| **Cloudflare Workers + Containers** | **$5/mo** (Workers Paid; Containers have no free tier) | Both real Docker images behind one Worker; needs Neon + Upstash | [`cloudflare/README.md`](cloudflare/README.md) |
| **Cloudflare Pages + Render** | **$0** | Frontend on Pages (edge), API on Render free + Neon + Upstash. Pages keeps working, though `next-on-pages` is deprecated upstream — a Workers migration needs Next 15.5+ for the current OpenNext adapter | [`deploy/README.md`](deploy/README.md) |

### Docker Compose (recommended for production)

```bash
cp .env.example .env
# APP_ENV=production, strong SECRET_KEY, real DATABASE_URL/REDIS_URL,
# provider keys, CORS_ORIGINS with your domain, TLS certs in infrastructure/nginx/certs/

docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose logs -f api worker     # confirm startup checks pass
```

The `api` service runs `alembic upgrade head` before serving. Scale workers with `docker compose up -d --scale worker=4`. TLS: drop `fullchain.pem` / `privkey.pem` into `infrastructure/nginx/certs/` and switch nginx to `nginx-tls.conf.example` — HSTS is only ever sent on the HTTPS entrypoint.

### Cloudflare Containers ($5/mo)

```bash
cd cloudflare && npm install && npx wrangler login
npx wrangler secret put SECRET_KEY        # plus DATABASE_URL, REDIS_URL, CORS_ORIGINS, FRONTEND_ORIGIN
npx wrangler deploy
```

Cloudflare has no managed Postgres, so pair it with Neon (Postgres) and Upstash (Redis). `max_instances: 1` keeps a job, its SSE stream and its report inside one container.

### Cloudflare Pages + Render ($0)

Frontend on Pages (`npm run pages:build`, root directory `frontend`), API on Render's free Docker service, Neon + Upstash for data. `frontend/wrangler.toml` pins the runtime requirement — the Pages bundle needs the **`nodejs_compat`** compatibility flag or the Worker throws at runtime. Free-tier trade-offs: the API sleeps after 15 idle minutes (~1 min cold start, 512 MB RAM). Full walkthrough: [`deploy/README.md`](deploy/README.md).

---

## Operations

| Task | Command |
|---|---|
| Logs (JSON lines, secrets redacted) | `docker compose logs -f api worker` |
| Health probe | `curl -s localhost:8000/api/health` → DB, Redis, workers, provider |
| Migrations | `docker compose exec api alembic upgrade head` (idempotent) |
| Database backup | `docker compose exec postgres pg_dump -U locallead locallead \| gzip > backup.sql.gz` |
| Upgrade | `git pull && docker compose up -d --build` |
| Provider spend | Admin → Overview (`/admin`) — calls and estimated cost today/month |
| Crawl audit | Per-business `crawl_pages` in the UI; `python -m leadgen audit` for the standalone engine |

Wire `/api/health` to your uptime monitor: it fails loudly when the database, Redis, worker presence or provider configuration is broken.

---

## Security posture

- **Passwords**: Argon2 (bcrypt fallback). **Sessions**: short-lived JWT access tokens + rotating refresh tokens with reuse detection, hashed at rest.
- **Authorization**: every job, business and export is ownership-scoped; cross-user access returns **404** (not 403) to avoid enumeration; `/api/admin/*` requires the ADMIN role.
- **SSRF guard** on the crawler: scheme allowlist, credential rejection, hostname blocklist, DNS resolution checks against loopback/RFC1918/CGNAT/link-local/metadata ranges, and **re-validation on every redirect hop** — all before any network request.
- **Crawler compliance**: robots.txt honoured and cached per origin, honest User-Agent, per-domain rate limiting, page/byte caps, HTML-only content gate, no JS evasion, no CAPTCHA solving, no proxy rotation.
- **Inputs**: Pydantic validation on every endpoint, SQLAlchemy bound parameters only.
- **Rate limits**: login/register/reset per IP, search creation per user, general API ceiling.
- **Secrets**: environment-only, validated at boot, registered for log redaction (`core/config.py → register_secret`).
- **Exports**: UUID filenames, ownership re-checked on every download, spreadsheet formula injection neutralized, HTML escaped in PDFs.
- **Headers**: CSP (host-based `connect-src`), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, HSTS on the TLS entrypoint only.

Full detail: [`docs/SECURITY.md`](docs/SECURITY.md). Production checklist: [`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Jobs stay `queued` forever | No Celery worker, and the in-process fallback could not start | Start the worker (`docker compose up worker`, or `python run.py --worker`); check `/api/health` → `workers` |
| `Refusing to start in production with missing configuration` | Startup validation caught a placeholder `SECRET_KEY` or a provider without its key | Set the named variable in `.env`; `PROVIDER_STRICT_CONFIG=false` relaxes it for staging |
| Browser shows an empty dashboard, console has CORS errors | API `CORS_ORIGINS` lacks the frontend origin | Add the exact origin (`https://your-app.pages.dev`) and redeploy the API |
| Export says *"Report not found"* | Container/report storage is ephemeral and the instance restarted | Regenerate the export (see limitations) |
| Cloudflare Pages 500 after deploy | Missing `nodejs_compat` compatibility flag | Set the flag (already pinned in `frontend/wrangler.toml`), rebuild |
| `Could not detect a directory containing static files` on Pages | Wrong build command/output for a Next.js app | Build with `npx @cloudflare/next-on-pages@1`, output `.vercel/output/static` |
| Everything slow on first request | Free tier cold start | Expected; the instance wakes in ~1 minute |
| `429` while testing | Rate limiting | Raise `RATE_LIMIT_SEARCH_PER_MIN` / `RATE_LIMIT_API_PER_MIN` locally |

More: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

---

## Known limitations

- **Single-provider geography.** Discovery quality depends on the provider's coverage; OSM (Nominatim) is free but sparse outside well-mapped cities.
- **No JavaScript rendering.** The crawler reads server-rendered HTML only. Contact pages rendered client-side are missed by design (no Playwright, no evasion).
- **Ephemeral storage on single-container hosts.** Generated reports live on local disk; Cloudflare Containers and Render discard them when the instance sleeps or redeploys — regenerate the export, or move exports to object storage (R2/S3) for permanence.
- **One API instance in the container deployments.** `max_instances: 1` keeps jobs, SSE and reports co-located; heavy parallel use needs the Celery worker and a shared Redis.
- **Email confidence is a heuristic.** Domain match + provenance + disposable-host check — strong signal, not a deliverability guarantee.

---

## The standalone `leadgen/` engine

`leadgen/` is a separate, self-contained Python package for single-lead or CSV-batch OSINT enrichment, with no Google Maps dependency and no paid calls required. It shares the platform's compliance model (robots.txt, SSRF guard, honest User-Agent) but runs standalone on `requests`, `pandas` and SQLite.

```bash
pip install -r requirements.txt

python -m leadgen run --name "Sunrise Hospital" --website https://sunrisehospital.in \
    --city Pune --country in --phone "+91 98220 12345"
python -m leadgen run --input seeds.csv      # columns: business_name,website,city,country_code,phone,address
python -m leadgen export                     # output/leads.json + output/leads.csv
python -m leadgen audit --limit 20           # Crawl Audit Trail
```

Notable behaviour: **email confidence** is tiered by whether the email's domain matches the business domain (HIGH), appears on the official site on a reputable free host (MEDIUM), or comes from an external/third-party page or a disposable host (LOW). **Every outbound HTTP call** is recorded in `crawl_audit_events` with status, latency, robots verdict and a `status_kind` (`ok`, `http_error`, `robots_disallowed`, `ssrf_blocked`, `dns_error`, `ssl_error`, `timeout`, `api_exempt`). **Deduplication** hashes normalized E.164 phone + domain, persisted across runs so duplicates are caught between batches, not just within one.

---

## Documentation map

| Document | Read it for |
|---|---|
| [`PROJECT_EXPLANATION.md`](PROJECT_EXPLANATION.md) | Concepts, data model, user journeys, background pipeline in depth |
| [`docs/architecture.md`](docs/architecture.md) | System diagram, provider abstraction, crawler compliance model, tables |
| [`docs/api.md`](docs/api.md) | Endpoint-by-endpoint reference with payloads |
| [`docs/deployment.md`](docs/deployment.md) | Compose deployment, TLS, hardening checklist |
| [`docs/HOSTINGER_DEPLOYMENT.md`](docs/HOSTINGER_DEPLOYMENT.md) | VPS walkthrough with free, keyless APIs |
| [`deploy/README.md`](deploy/README.md) | $0 split: Cloudflare Pages + Render + Neon + Upstash |
| [`cloudflare/README.md`](cloudflare/README.md) | $5/mo all-Cloudflare Containers deployment |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Auth, RBAC, SSRF matrix, data integrity, headers |
| [`docs/TESTING.md`](docs/TESTING.md) | Suites, harnesses, conventions |
| [`docs/CRAWLER.md`](docs/CRAWLER.md) | Crawl policy and extraction rules |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Failure modes and fixes |
| [`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md) | Pre-launch checklist |

---

## Contributing

1. Branch from `main` (`feat/…`, `fix/…`).
2. Keep the provider interface intact — a new data source is one class plus a factory registration in `app/providers/`.
3. Match existing conventions: typed API client in `frontend/lib/api.ts`, Pydantic schemas for every payload, no secrets in code.
4. Run the suites before opening a PR:

```bash
cd backend && ./.venv/bin/python -m pytest -q && cd ../frontend && npm run build
```

5. Describe the behaviour change and how you verified it. Fix failing checks at the root cause rather than relaxing them.

**Never commit** `.env`, credentials, generated reports or `node_modules`.

---

## License

MIT — see [`LICENSE`](LICENSE).

Data and services used by the platform keep their own terms: Google Places API, Geoapify and Serper require accounts and are billed by their providers; OpenStreetMap data is © OpenStreetMap contributors, available under the [ODbL](https://www.openstreetmap.org/copyright) and used via Nominatim under its [usage policy](https://operations.osmfoundation.org/policies/nominatim/).
