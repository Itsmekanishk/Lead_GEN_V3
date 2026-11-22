/**
 * LocalLead — Cloudflare Containers front door.
 *
 * Two containers are built from the Dockerfiles already in this repo:
 *   ApiContainer → FastAPI/uvicorn on :8000  (backend/)
 *   WebContainer → Next.js on :3000          (frontend/)
 *
 * This Worker is the only public entrypoint:
 *   /backend/*  → API container, with the /backend prefix stripped
 *                 (the same mapping nginx does in docker-compose)
 *   everything  → web container
 *
 * Both services are addressed by a fixed Durable Object name ("api" / "web"),
 * so every request lands on the same container instance. That keeps a search
 * job's in-process worker thread together with its SSE progress stream, and
 * keeps a generated report downloadable until the container idles out.
 */
import { DurableObject } from "cloudflare:workers";

interface Env {
  API: DurableObjectNamespace;
  WEB: DurableObjectNamespace;

  // Worker secrets — public variables are optional.
  DATABASE_URL: string;
  REDIS_URL: string;
  SECRET_KEY: string;
  CORS_ORIGINS: string;
  FRONTEND_ORIGIN: string;
  ENCRYPTION_KEY?: string;
  BUSINESS_PROVIDER?: string;
  GOOGLE_MAPS_API_KEY?: string;
  SERPER_API_KEY?: string;
  GEOAPIFY_API_KEY?: string;
  APILAYER_API_KEY?: string;
  NUMVERIFY_API_KEY?: string;
  SMTP_HOST?: string;
  SMTP_PORT?: string;
  SMTP_USER?: string;
  SMTP_PASSWORD?: string;
  SMTP_FROM?: string;
}

const API_PORT = 8000;
const WEB_PORT = 3000;

// Containers stop after this long without traffic. 6h is the platform maximum,
// which keeps a freshly generated report downloadable for a working session.
const API_IDLE_MS = 6 * 60 * 60 * 1000;
const WEB_IDLE_MS = 60 * 60 * 1000;

/** Drop unset values: container env must be plain strings. */
function defined(values: Record<string, string | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    if (typeof value === "string" && value.length > 0) out[key] = value;
  }
  return out;
}

/** Boot the container once, wait for its HTTP server, then reuse it. */
async function startAndWait(
  ctx: DurableObjectState,
  options: {
    port: number;
    readyPath: string;
    idleMs: number;
    env: Record<string, string>;
    entrypoint?: string[];
  },
): Promise<void> {
  const container = ctx.container;
  if (!container) throw new Error("No container is attached to this Durable Object");

  if (!container.running) {
    container.start({
      enableInternet: true, // crawler, providers, Postgres, Redis
      env: options.env,
      ...(options.entrypoint ? { entrypoint: options.entrypoint } : {}),
    });
    // Surface crashes in `wrangler tail` instead of failing silently.
    ctx.waitUntil(
      container.monitor().then(
        () => console.log("container exited"),
        (error: unknown) => console.error("container failed", error),
      ),
    );
  }

  await container.setInactivityTimeout(options.idleMs);

  const port = container.getTcpPort(options.port);
  for (let attempt = 0; attempt < 180; attempt++) {
    try {
      const response = await port.fetch(`http://container${options.readyPath}`, {
        signal: AbortSignal.timeout(2500),
      });
      await response.body?.cancel();
      if (response.ok) return;
    } catch {
      // The port is not listening yet — keep polling.
    }
    await scheduler.wait(500);
  }
  throw new Error(`Container did not become ready on :${options.port}${options.readyPath}`);
}

/** Forward the incoming request to the container's HTTP port. */
function forward(ctx: DurableObjectState, request: Request, port: number): Promise<Response> {
  const container = ctx.container;
  if (!container) throw new Error("No container is attached to this Durable Object");

  const url = new URL(request.url);
  url.protocol = "http:";
  url.host = "container";
  const forwarded = new Request(url, request);
  forwarded.headers.delete("host");
  return container.getTcpPort(port).fetch(forwarded);
}

export class ApiContainer extends DurableObject<Env> {
  private starting: Promise<void> | undefined;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    if (ctx.container?.running) {
      void ctx.blockConcurrencyWhile(() => ctx.container!.setInactivityTimeout(API_IDLE_MS));
    }
  }

  async fetch(request: Request): Promise<Response> {
    this.starting ??= startAndWait(this.ctx, {
      port: API_PORT,
      readyPath: "/api/health",
      idleMs: API_IDLE_MS,
      // Same variables docker-compose passes to the `api` service, with the
      // datastores moved to external providers (Cloudflare has no Postgres or
      // Redis). APP_ENV=production enables the backend's fail-fast config check.
      env: defined({
        APP_ENV: "production",
        DATABASE_URL: this.env.DATABASE_URL,
        REDIS_URL: this.env.REDIS_URL,
        SECRET_KEY: this.env.SECRET_KEY,
        ENCRYPTION_KEY: this.env.ENCRYPTION_KEY,
        CORS_ORIGINS: this.env.CORS_ORIGINS,
        FRONTEND_ORIGIN: this.env.FRONTEND_ORIGIN,
        BUSINESS_PROVIDER: this.env.BUSINESS_PROVIDER ?? "nominatim",
        // No Celery worker runs here: dispatch_search_job() detects no live
        // worker and executes the pipeline in-process instead.
        PROVIDER_STRICT_CONFIG: "false",
        PDF_STORAGE_PATH: "/srv/data/reports",
        GOOGLE_MAPS_API_KEY: this.env.GOOGLE_MAPS_API_KEY,
        SERPER_API_KEY: this.env.SERPER_API_KEY,
        GEOAPIFY_API_KEY: this.env.GEOAPIFY_API_KEY,
        APILAYER_API_KEY: this.env.APILAYER_API_KEY,
        NUMVERIFY_API_KEY: this.env.NUMVERIFY_API_KEY,
        SMTP_HOST: this.env.SMTP_HOST,
        SMTP_PORT: this.env.SMTP_PORT,
        SMTP_USER: this.env.SMTP_USER,
        SMTP_PASSWORD: this.env.SMTP_PASSWORD,
        SMTP_FROM: this.env.SMTP_FROM,
      }),
      // Compose's api command, unchanged: migrate, then serve.
      entrypoint: [
        "sh",
        "-c",
        "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips=*",
      ],
    }).finally(() => {
      this.starting = undefined;
    });

    await this.starting;
    return forward(this.ctx, request, API_PORT);
  }
}

export class WebContainer extends DurableObject<Env> {
  private starting: Promise<void> | undefined;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    if (ctx.container?.running) {
      void ctx.blockConcurrencyWhile(() => ctx.container!.setInactivityTimeout(WEB_IDLE_MS));
    }
  }

  async fetch(request: Request): Promise<Response> {
    this.starting ??= startAndWait(this.ctx, {
      port: WEB_PORT,
      readyPath: "/",
      idleMs: WEB_IDLE_MS,
      env: defined({ NODE_ENV: "production", PORT: String(WEB_PORT) }),
    }).finally(() => {
      this.starting = undefined;
    });

    await this.starting;
    return forward(this.ctx, request, WEB_PORT);
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/backend" || url.pathname.startsWith("/backend/")) {
      const target = new URL(request.url);
      target.pathname = url.pathname.slice("/backend".length) || "/";
      return env.API.getByName("api").fetch(new Request(target.toString(), request));
    }

    return env.WEB.getByName("web").fetch(request);
  },
} satisfies ExportedHandler<Env>;
