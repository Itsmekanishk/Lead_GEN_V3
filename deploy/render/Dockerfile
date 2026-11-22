# LocalLead API for Render's free web service.
#
# Same application as backend/, packaged for a repo-root build context so Render
# (dockerfilePath: deploy/render/Dockerfile) can build it without a custom
# context. No Celery worker: the API runs search jobs in-process when no worker
# answers (see app/workers/dispatch.py), which is required on a single free
# instance.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ ./

RUN useradd -m -u 10001 appuser \
    && mkdir -p /srv/data/reports \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-10000}/api/health" || exit 1

# Render provides $PORT at runtime; migrations run before serving.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips=*"]
