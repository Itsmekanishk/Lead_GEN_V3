"""Export service: orchestrates format generators and records exports."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.models.models import Business, Export, SearchJob
from app.exports.pdf_report import build_pdf_report
from app.exports.csv_export import build_csv
from app.exports.xlsx_export import build_xlsx


class ExportError(RuntimeError):
    """Raised when report generation fails."""


def _unique_businesses(db: DBSession, job_id: str) -> list[Business]:
    """Canonical (non-duplicate) businesses ordered by name."""
    return (
        db.query(Business)
        .filter(Business.job_id == job_id, Business.dedup_status == "unique")
        .order_by(Business.name.asc())
        .all()
    )


def generate_export(db: DBSession, job: SearchJob, fmt: str,
                    user_id: str) -> tuple[str, int]:
    """Generate the export file for *job*; returns (filename, row_count).

    Files are written under PDF_STORAGE_PATH with unguessable UUID names;
    download authorization is enforced by the API layer.
    """
    settings = get_settings()
    if fmt not in ("pdf", "csv", "xlsx"):
        raise ExportError(f"Unsupported format '{fmt}'")
    businesses = _unique_businesses(db, job.id)
    if not businesses:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="No results to export for this search")

    out_dir = Path(settings.pdf_storage_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"report_{uuid.uuid4()}.{fmt}"
    out_path = out_dir / filename

    parsed = (job.parsed_category, job.parsed_location)
    if fmt == "pdf":
        build_pdf_report(
            out_path,
            search_query=job.query_raw,
            category=job.parsed_category,
            location=job.parsed_location,
            businesses=businesses,
            provider=job.provider,
        )
    else:
        rows = _export_rows(db, businesses, job)
        if fmt == "csv":
            build_csv(out_path, rows)
        else:
            build_xlsx(out_path, rows)

    row_count = len(businesses)
    db.add(Export(
        job_id=job.id, user_id=user_id, format=fmt,
        filename=filename, row_count=row_count,
    ))
    db.commit()
    return filename, row_count


def _export_rows(db: DBSession, businesses: list[Business], job: SearchJob) -> list[dict]:
    """Flatten businesses into export-row dicts."""
    rows = []
    for b in businesses:
        rows.append({
            "Business Name": b.name,
            "Category": b.category,
            "Address": b.address,
            "Phone": b.phone_normalized or b.phone,
            "Email": b.email,
            "Website": b.website or b.website_normalized,
            "Rating": b.rating,
            "Review Count": b.review_count,
            "Email Confidence": b.email_confidence,
            "Phone Source": b.phone_source or ("provider" if b.phone else ""),
            "Website Source": b.source,
            "Search Query": job.query_raw,
            "Search Location": job.parsed_location,
            "Collected At": (b.updated_at or b.created_at),
        })
    return rows


def resolve_download(db: DBSession, job_id: str, filename: str,
                     user) -> Path:
    """Authorize a report download: owner or admin, name must match job export.

    Never serves arbitrary paths — the filename must be a registered Export row
    for this job.
    """
    row = db.query(Export).filter(
        Export.job_id == job_id,
        Export.filename == filename,
        Export.user_id == user.id,
    ).first()
    if row is None:
        if not user.is_admin:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report not found")
        row = db.query(Export).filter(
            Export.job_id == job_id, Export.filename == filename
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report not found")
    path = (Path(get_settings().pdf_storage_path) / row.filename).resolve()
    base = Path(get_settings().pdf_storage_path).resolve()
    if base not in path.parents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    return path
