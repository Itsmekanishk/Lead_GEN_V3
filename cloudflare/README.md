# LocalLead on Cloudflare Containers

This directory deploys the app you already have — the same `backend/` and
`frontend/` Dockerfiles — onto Cloudflare. No application code is modified;
everything here is deployment configuration.

```
browser
  │
  ▼
Worker (worker.ts)  ── /backend/* ──▶  ApiContainer  (uvicorn :8000, backend/)
  │                                     └─ Alembic migrations on boot,
  └──────── everything else ─────────▶  WebContainer  (next start :3000, frontend/)
                                          ▲
                    Neon Postgres ◀───────┘──────▶ Upstash Redis (SSE progress)
```

Why this shape:

- **Cloudflare Pages cannot run this app.** It only serves static files, and the
  repo has no static build — that is exactly the `Could not detect a directory
  containing static files` error. Containers run the real images.
- **No Celery worker container.** `backend/app/workers/dispatch.py` falls back to
  an in-process thread whenever no Celery worker answers a ping, so the API runs
  the pipeline itself. Redis is still used for SSE progress events.
- **One instance each.** Requests are pinned to the Durable Objects named `api`
  and `web`, so a job's worker thread, its SSE stream and its generated report
  all live in the same container.

## 0. Prerequisites

| Requirement | Why |
|---|---|
| **Workers Paid plan** ($5/mo) | Containers are not available on the free plan |
| **Docker running locally** | `wrangler deploy` builds both images and pushes them |
| **Neon** (or any Postgres) free tier | Cloudflare has no managed Postgres |
| **Upstash Redis** free tier | Redis 7 replacement for SSE progress + broker |
| Node 20+ | Wrangler |

```bash
cd cloudflare
npm install
npx wrangler login
```

## 1. Create the datastores

**Postgres (Neon)** — create a project, copy the pooled connection string, then
rewrite the scheme for SQLAlchemy/psycopg2 and keep TLS on:

```
postgresql+psycopg2://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

**Redis (Upstash)** — create a Redis database (TLS enabled), copy the
`rediss://default:PASSWORD@HOST:6379` URL. The free tier is enough: this is used
for progress pub/sub, not job execution.

## 2. Set the Worker secrets

`wrangler secret put` prompts for the value; nothing is written to git.

```bash
npx wrangler secret put SECRET_KEY        # openssl rand -hex 32
npx wrangler secret put DATABASE_URL      # postgresql+psycopg2://…?sslmode=require
npx wrangler secret put REDIS_URL         # rediss://default:…@host:6379
npx wrangler secret put CORS_ORIGINS      # https://yourdomain.com
npx wrangler secret put FRONTEND_ORIGIN   # https://yourdomain.com
```

Optional, only if you use them (see `secrets.example`): `ENCRYPTION_KEY`,
`BUSINESS_PROVIDER`, `GOOGLE_MAPS_API_KEY`, `SERPER_API_KEY`,
`GEOAPIFY_API_KEY`, `APILAYER_API_KEY`, `NUMVERIFY_API_KEY`, `SMTP_*`.

Notes:

- `APP_ENV=production` is set by the Worker, so the backend fails fast on a
  placeholder `SECRET_KEY` — that is intended.
- The database schema is created by `alembic upgrade head`, which the Worker
  runs as the container's entrypoint before starting uvicorn.

## 3. Deploy

```bash
npx wrangler deploy
```

Wrangler builds and pushes both images (first build is the slow one), uploads the
Worker, and provisions the containers. **Wait a few minutes after the first
deploy** — the URL can answer while containers are still provisioning.

Check status:

```bash
npx wrangler containers list
npx wrangler tail            # Worker + container logs
```

Smoke test the default `https://locallead.<your-subdomain>.workers.dev`:

```bash
curl -s https://locallead.<your-subdomain>.workers.dev/backend/api/health
# {"status":"ok","app_env":"production","checks":{
#    "database":"ok","redis":"ok","workers":"no_workers","provider":"ok"}}
```

`workers: "no_workers"` is expected — jobs run in-process. Then open the URL,
register a user, run a search, and confirm the live progress panel updates.

## 4. Attach your domain

1. Cloudflare dashboard → **Workers & Pages** → `locallead`.
2. **Settings → Domains & Routes → Add → Custom domain**.
3. Enter `yourdomain.com` (repeat for `www` if you want it).
4. TLS is automatic; keep SSL/TLS mode **Full (strict)**.

Then make sure `CORS_ORIGINS` and `FRONTEND_ORIGIN` contain the final
`https://yourdomain.com` (update the secret and redeploy if you set them before
choosing the domain):

```bash
npx wrangler secret put CORS_ORIGINS
npx wrangler deploy
```

## 5. Remove the project that was failing

The old Git-connected Worker/Pages project cannot ever build this repo. In the
dashboard: **Workers & Pages** → select it → **Settings** → **Builds →
Disconnect**, then scroll to **Delete** (Pages projects: **Settings → Delete
project**). That stops the failed `npx wrangler deploy` builds; this directory
replaces it.

## Costs

$5/mo Workers Paid includes 25 GiB-hours of memory and 375 vCPU-minutes of
container time. Beyond that, containers bill only while awake; both idle out
(API after 6h, web after 1h) so light use stays near the base price. Always-on
`standard-1` + `basic` runs roughly $20–35/mo.

## Known limitations

- **One API instance.** `max_instances: 1` plus a single Durable Object name is
  what keeps jobs, SSE and exports co-located. Two searches run concurrently as
  threads in that one container; heavy parallel use needs the Celery worker back
  (and a queue Redis can reach).
- **Ephemeral disk.** Reports written to `/srv/data/reports` survive until the
  API container sleeps or is redeployed. Regenerate the export if a download
  says "Report not found". Move exports to R2 if you need them to persist.
- **Container cold start.** The first request after idling takes a few seconds
  while the image boots.
- **No `docker-compose`, no nginx.** nginx's routing is done by `worker.ts`; the
  compose file is untouched and still works for local/VPS use.
