"""Job dispatch indirection.

Production dispatches the Celery task; tests can monkeypatch
``app.workers.dispatch.dispatch_search_job`` to run the pipeline inline.
"""
from __future__ import annotations


import logging
import threading
import time

log = logging.getLogger("locallead.dispatch")

# A control ping costs up to ~0.5s, and searches are created rarely, so the
# worker liveness answer is cached for a few seconds.
_WORKER_CACHE: dict[str, float | bool] = {"alive": False, "checked_at": 0.0}
_WORKER_CACHE_TTL_SECONDS = 5.0


def _is_redis_available() -> bool:
    try:
        import redis
        from app.core.config import get_settings

        client = redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=0.2, socket_timeout=0.2
        )
        return bool(client.ping())
    except Exception:
        return False


def _celery_workers_alive() -> bool:
    """True when at least one Celery worker answers a control ping.

    Without this check a reachable broker with no worker subscribed accepted
    the task and the job sat in ``queued`` forever with no error anywhere.
    """
    now = time.monotonic()
    if now - float(_WORKER_CACHE["checked_at"]) < _WORKER_CACHE_TTL_SECONDS:
        return bool(_WORKER_CACHE["alive"])
    try:
        from app.workers.celery_app import celery_app

        alive = bool(celery_app.control.inspect(timeout=0.5).ping())
    except Exception:
        alive = False
    _WORKER_CACHE["alive"] = alive
    _WORKER_CACHE["checked_at"] = now
    return alive


def _run_inline(job_id: str) -> None:
    try:
        from app.workers.tasks import run_search_job

        log.info("Running job %s in local background thread", job_id)
        run_search_job(job_id)
    except Exception as exc:
        log.exception("Local thread worker failed for job %s: %s", job_id, exc)


def dispatch_search_job(job_id: str) -> None:
    """Send the search job to Celery, falling back to an in-process thread.

    The fallback applies when there is no broker *and* when the broker is
    reachable but no worker is consuming from it — otherwise a local run with
    Redis installed and no worker would queue jobs that never start.
    """
    if _is_redis_available() and _celery_workers_alive():
        try:
            from app.workers.celery_app import celery_app

            celery_app.send_task("locallead.run_search_job", args=[job_id])
            return
        except Exception as exc:
            log.warning("Celery dispatch failed (%s); falling back to background thread", exc)

    log.info(
        "No Celery worker consuming from the broker; dispatching job %s to a local thread",
        job_id,
    )
    thread = threading.Thread(target=_run_inline, args=(job_id,), daemon=True)
    thread.start()
