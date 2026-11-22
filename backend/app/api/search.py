"""Search job endpoints: interpret → create → track → results → cancel."""
from __future__ import annotations

from typing import Optional

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.deps import CurrentUser
from app.core.rate_limiter import rate_limit_dependency, search_limit_from_env
from app.models.database import get_db
from app.models.models import Business, SearchJob
from app.schemas.search import (
    BusinessDetailOut,
    BusinessOut,
    InterpretedQuery,
    JobCreatedResponse,
    JobOut,
    JobProgress,
    PaginatedBusinesses,
    ResultsSummary,
    SearchCreateRequest,
    SearchInterpretResponse,
)
from app.services.query_parser import parse_query
from app.utils.datetime_utils import iso

router = APIRouter(prefix="/api/search", tags=["search"])

StatusFilter = str  # documented values in Query(...) below


def _job_or_404(db: DBSession, job_id: str, user: CurrentUser) -> SearchJob:
    job = db.get(SearchJob, job_id)
    if job is None or (job.user_id != user.id and not user.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Search not found")
    return job


@router.post("/interpret", response_model=SearchInterpretResponse)
def interpret_search(body: SearchCreateRequest, user: CurrentUser) -> SearchInterpretResponse:
    """Cheap preview of how the parser understood the query — no job created."""
    settings = get_settings()
    raw = body.free_text or (
        f"{body.category or ''} | {body.location or ''}".strip(" |")
    )
    parsed = parse_query(raw)
    if body.category and body.location:
        parsed.category = body.category
        parsed.location = body.location
        parsed.confidence = "high"
    return SearchInterpretResponse(
        interpreted=InterpretedQuery(**parsed.interpreted()),
        estimated_max_results=min(body.max_results, settings.max_results),
    )


@router.post("", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit_dependency(search_limit_from_env))])
def create_search(body: SearchCreateRequest, user: CurrentUser,
                  db: DBSession = Depends(get_db)) -> JobCreatedResponse:
    """Create an async search job; returns immediately with job_id."""
    settings = get_settings()
    if not body.free_text and not (body.category and body.location):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide free_text, or both category and location",
        )

    raw = body.free_text or f"{body.category} | {body.location}"
    parsed = parse_query(raw)
    if body.category and body.location:
        parsed.category = body.category
        parsed.category_slug = body.category.lower()
        parsed.location = body.location
        parsed.confidence = "high"

    effective_max = min(body.max_results, settings.max_results)
    job = SearchJob(
        user_id=user.id,
        query_raw=raw[:500],
        parsed_category=(body.category or parsed.category or "")[:250],
        parsed_location=(body.location or parsed.location_text or "")[:250],
        parsed_latitude=None,
        parsed_longitude=None,
        max_results=effective_max,
        requested_results=body.max_results,
        radius_m=body.radius_m,
        country=body.country.lower(),
        language=body.language,
        provider=(body.provider or settings.business_provider),
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch to the queue is best-effort: if the broker is unreachable the
    # job stays 'queued' in the DB (workers re-scan for queued jobs) and the
    # API still returns 202 — a broker outage must not 500 a valid request.
    import logging

    from app.workers.dispatch import dispatch_search_job

    try:
        dispatch_search_job(job.id)
    except Exception:  # noqa: BLE001 — broker down / result-backend errors
        logging.getLogger("locallead.api.search").warning(
            "dispatch_failed_job_left_queued", extra={"job_id": job.id},
            exc_info=True,
        )
    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("", response_model=list[JobOut])
def list_my_searches(user: CurrentUser, db: DBSession = Depends(get_db),
                     limit: int = Query(default=50, ge=1, le=200)) -> list[JobOut]:
    """List the current user's searches (newest first)."""
    jobs = db.query(SearchJob).filter(SearchJob.user_id == user.id) \
        .order_by(SearchJob.created_at.desc()).limit(limit).all()
    return [_job_out(j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: CurrentUser,
            db: DBSession = Depends(get_db)) -> JobOut:
    """Status + progress of one search job."""
    job = _job_or_404(db, job_id, user)
    return _job_out(job)


@router.get("/{job_id}/results", response_model=PaginatedBusinesses)
def get_results(
    job_id: str,
    user: CurrentUser,
    db: DBSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    has_email: Optional[bool] = Query(default=None),
    has_phone: Optional[bool] = Query(default=None),
    has_website: Optional[bool] = Query(default=None),
    high_confidence_email: Optional[bool] = Query(default=None),
    category: Optional[str] = Query(default=None, max_length=100),
    min_rating: Optional[float] = Query(default=None, ge=0, le=5),
    q: Optional[str] = Query(default=None, max_length=100),
    dedup: str = Query(default="unique", pattern="^(unique|all|duplicates)$"),
) -> PaginatedBusinesses:
    """Filterable, sortable, paginated result rows for a completed job."""
    job = _job_or_404(db, job_id, user)
    query = db.query(Business).filter(Business.job_id == job.id)

    if dedup == "unique":
        query = query.filter(Business.dedup_status == "unique")
    elif dedup == "duplicates":
        query = query.filter(Business.dedup_status == "duplicate")

    if has_email is True:
        query = query.filter(Business.email != "")
    elif has_email is False:
        query = query.filter(Business.email == "")
    if has_phone is True:
        query = query.filter(Business.phone_normalized != "")
    elif has_phone is False:
        query = query.filter(Business.phone_normalized == "")
    if has_website is True:
        query = query.filter(Business.website_normalized != "")
    elif has_website is False:
        query = query.filter(Business.website_normalized == "")
    if high_confidence_email:
        query = query.filter(Business.email_confidence == "HIGH")
    if category:
        query = query.filter(Business.category.ilike(f"%{category}%"))
    if min_rating is not None:
        query = query.filter(Business.rating >= min_rating)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Business.name.ilike(like),
            Business.address.ilike(like),
            Business.email.ilike(like),
        ))

    total = query.count()
    items = (query
             .order_by(Business.dedup_status.asc(), Business.name.asc())
             .offset((page - 1) * page_size)
             .limit(page_size)
             .all())
    return PaginatedBusinesses(
        items=[BusinessOut.model_validate(b) for b in items],
        total=total, page=page, page_size=page_size,
        pages=(total + page_size - 1) // page_size,
        requested_results=job.requested_results or job.max_results,
    )


@router.get("/{job_id}/summary", response_model=ResultsSummary)
def get_summary(job_id: str, user: CurrentUser,
                db: DBSession = Depends(get_db)) -> ResultsSummary:
    """Summary card data: totals, contact yields, duplicates removed."""
    job = _job_or_404(db, job_id, user)
    businesses = db.query(Business).filter(Business.job_id == job.id).all()
    unique = [b for b in businesses if b.dedup_status == "unique"]
    summary = {
        "total": len(businesses),
        "unique": len(unique),
        "duplicates_removed": len(businesses) - len(unique),
        "with_phone": sum(1 for b in unique if b.phone_normalized),
        "with_website": sum(1 for b in unique if b.website_normalized),
        "with_email": sum(1 for b in unique if b.email),
        "high_confidence_emails": sum(
            1 for b in unique if b.email and b.email_confidence == "HIGH"),
        "failed_websites": sum(
            1 for b in unique
            if b.crawl_pages and all(p.status not in ("ok", "robots_disallowed")
                                     for p in b.crawl_pages)),
    }
    return ResultsSummary(job=_job_out(job), summary=summary)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, user: CurrentUser,
               db: DBSession = Depends(get_db)) -> JobOut:
    """Request cancellation; the worker honours it between stages."""
    job = _job_or_404(db, job_id, user)
    if job.status in ("completed", "failed", "cancelled"):
        return _job_out(job)
    job.status = "cancelled"
    job.stage_detail = "Cancelled by user"
    job.finished_at = None
    db.commit()
    db.refresh(job)
    # Push the cancellation to SSE listeners immediately instead of waiting
    # for the keep-alive DB poll (~5s).
    from app.workers.progress import publish_job_event

    publish_job_event({
        "job_id": job.id, "status": "cancelled",
        "stage_detail": job.stage_detail, "progress": job.progress,
        "total": job.total, "completed": job.completed, "failed": job.failed,
        "emails_found": job.emails_found, "phones_found": job.phones_found,
        "websites_found": job.websites_found,
    })
    return _job_out(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, user: CurrentUser,
               db: DBSession = Depends(get_db)) -> Response:
    """Delete a finished search and all dependent rows (businesses, exports)."""
    job = _job_or_404(db, job_id, user)
    if job.status not in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Cancel the running search before deleting it",
        )
    from app.services import auth_service

    auth_service.audit(db, user_id=user.id, action="delete_search",
                       entity="search_job", entity_id=job.id)
    db.delete(job)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{job_id}/stream")
async def stream_progress(job_id: str, user: CurrentUser,
                          db: DBSession = Depends(get_db)) -> StreamingResponse:
    """Server-Sent Events stream of live job progress."""
    job = _job_or_404(db, job_id, user)

    async def event_gen():
        payload = {
            "job_id": job.id, "status": job.status,
            "stage_detail": job.stage_detail, "progress": job.progress,
            "total": job.total, "completed": job.completed,
            "failed": job.failed, "emails_found": job.emails_found,
            "phones_found": job.phones_found, "websites_found": job.websites_found,
        }
        yield f"data: {json.dumps(payload)}\n\n"
        if job.status in ("completed", "failed", "cancelled"):
            yield "event: done\ndata: {}\n\n"
            return

        # subscribe to redis pub/sub, with DB polling fallback for Redis-less dev
        terminal = {"completed", "failed", "cancelled"}
        redis_connected = False
        try:
            import redis.asyncio as aioredis

            from app.core.config import get_settings as _gs

            client = aioredis.from_url(_gs().redis_url, decode_responses=True, socket_connect_timeout=0.3)
            pubsub = client.pubsub()
            await pubsub.subscribe("locallead:job_events")
            redis_connected = True
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True,
                                               timeout=2.0)
                if msg is None:
                    yield ": keep-alive\n\n"
                    # check DB for terminal state in case events were missed;
                    # expire_all() bypasses the stale identity-map object
                    db.expire_all()
                    fresh = db.get(SearchJob, job.id)
                    if fresh and fresh.status in terminal:
                        yield f"event: done\ndata: {json.dumps({'status': fresh.status})}\n\n"
                        return
                    continue
                data = json.loads(msg["data"])
                if data.get("job_id") != job.id:
                    continue
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("status") in terminal:
                    yield "event: done\ndata: {}\n\n"
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # Fallback for Redis-less local development: poll DB directly
            while True:
                await asyncio.sleep(1.0)
                db.expire_all()
                fresh = db.get(SearchJob, job.id)
                if not fresh:
                    return
                data = {
                    "job_id": fresh.id, "status": fresh.status,
                    "stage_detail": fresh.stage_detail, "progress": fresh.progress,
                    "total": fresh.total, "completed": fresh.completed,
                    "failed": fresh.failed, "emails_found": fresh.emails_found,
                    "phones_found": fresh.phones_found, "websites_found": fresh.websites_found,
                }
                yield f"data: {json.dumps(data)}\n\n"
                if fresh.status in terminal:
                    yield f"event: done\ndata: {json.dumps({'status': fresh.status})}\n\n"
                    return
        finally:
            if redis_connected:
                try:
                    await pubsub.unsubscribe()
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _job_out(job: SearchJob) -> JobOut:
    return JobOut(
        id=job.id,
        query_raw=job.query_raw,
        parsed_category=job.parsed_category,
        parsed_location=job.parsed_location,
        status=job.status,
        stage_detail=job.stage_detail,
        progress=job.progress,
        provider=job.provider,
        max_results=job.max_results,
        requested_results=job.requested_results,
        created_at=iso(job.created_at),
        started_at=iso(job.started_at),
        finished_at=iso(job.finished_at),
        error_message=job.error_message,
        progress_stats=JobProgress(
            total=job.total, completed=job.completed, failed=job.failed,
            emails_found=job.emails_found, phones_found=job.phones_found,
            websites_found=job.websites_found,
        ),
    )
