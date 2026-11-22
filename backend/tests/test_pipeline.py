"""End-to-end pipeline test with the mock provider (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.base import Base  # noqa: E402
from app.models.database import get_engine, session_scope  # noqa: E402
from app.models.models import Business, BusinessContact, SearchJob, User  # noqa: E402
from app.services.export_service import generate_export  # noqa: E402
from app.workers.pipeline import _apply_scan_results, run_pipeline  # noqa: E402


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/pipe.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", str(tmp_path / "reports"))
    monkeypatch.setenv("BUSINESS_PROVIDER", "mock")
    monkeypatch.setenv("ENABLE_WEBSITE_CRAWLING", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models import database as db_mod

    db_mod.reset_engine()
    Base.metadata.create_all(db_mod.get_engine())
    return db_mod


def test_full_pipeline_completes(monkeypatch, tmp_path):
    db_mod = _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="pipe@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Hospitals in Porwal Road Pune",
                        parsed_category="Hospitals", parsed_location="Porwal Road, Pune",
                        max_results=12, provider="mock")
        db.add(job)
        db.commit()
        job_id = job.id
        user_id = user.id

    with session_scope() as db:
        run_pipeline(db, job_id)

    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        assert job.status == "completed"
        assert job.total == 12
        assert job.completed == 12
        businesses = db.query(Business).filter_by(job_id=job_id).all()
        assert len(businesses) == 12
        # phones normalized by the provider path
        assert sum(1 for b in businesses if b.phone_normalized) > 0
        # every business keeps provenance
        assert all(b.source for b in businesses)

        # exports work on the completed job
        reports_dir = tmp_path / "reports"
        for fmt in ("pdf", "csv", "xlsx"):
            filename, rows = generate_export(db, job, fmt, user_id)
            assert filename.startswith("report_") and rows == 12
            path = reports_dir / filename
            assert path.exists()


def test_contact_persistence_merges_phone_only_and_email_rows(monkeypatch, tmp_path):
    """Regression: business_contacts has UNIQUE (business_id, email) while
    email defaults to "" — a business with a provider phone AND a website
    phone used to insert two empty-email rows and crash the whole job with
    an IntegrityError at flush. Website contacts must merge into existing
    rows (same email → upgrade confidence; second phone → keep first row).
    """
    db_mod = _setup(monkeypatch, tmp_path)

    with session_scope() as db:
        user = User(email="merge@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Clinics in Pune",
                        parsed_category="Clinic", parsed_location="Pune",
                        max_results=5, provider="mock")
        db.add(job)
        db.flush()
        biz = Business(job_id=job.id, provider="mock", provider_place_id="merge_1",
                       name="Merge Clinic", category="Clinic",
                       phone="+919876543210", phone_normalized="+919876543210",
                       phone_source="provider",
                       website="https://mergeclinic.example.com")
        db.add(biz)
        db.flush()
        db.add(BusinessContact(business_id=biz.id, phone="+919876543210",
                               phone_normalized="+919876543210", source="provider",
                               confidence="HIGH"))
        db.commit()
        job_id, biz_id = job.id, biz.id

    scan = {
        "pages": [{"url": "https://mergeclinic.example.com/contact",
                   "page_type": "contact", "status": "ok", "http_status": 200,
                   "emails": 1, "phones": 2, "duration_ms": 10}],
        "emails": [
            {"email": "info@mergeclinic.example.com",
             "source_url": "https://mergeclinic.example.com/contact",
             "confidence": "MEDIUM", "context": "", "disposable": None},
            {"email": "info@mergeclinic.example.com",
             "source_url": "https://mergeclinic.example.com/about",
             "confidence": "HIGH", "context": "", "disposable": False},
        ],
        "phones": ["+91 98220 12345", "+91 98220 67890"],
        "ok": True,
        "summary": "1/1 pages ok, 1 emails, 2 phones",
    }
    with session_scope() as db:
        biz = db.get(Business, biz_id)
        _apply_scan_results(db, biz, scan)  # must not raise IntegrityError
        db.commit()

    with session_scope() as db:
        contacts = db.query(BusinessContact).filter_by(business_id=biz_id).all()
        emails = [c.email for c in contacts if c.email]
        assert emails.count("info@mergeclinic.example.com") == 1
        merged = next(c for c in contacts if c.email == "info@mergeclinic.example.com")
        assert merged.confidence == "HIGH"       # upgraded from the MEDIUM hit
        assert merged.email_disposable is False  # verdict recorded on merge
        phone_rows = [c for c in contacts if c.phone and not c.email]
        assert len(phone_rows) == 1              # merged, not duplicated
        assert phone_rows[0].phone == "+919876543210"
        assert phone_rows[0].source == "provider"
        biz = db.get(Business, biz_id)
        assert biz.email == "info@mergeclinic.example.com"
        assert biz.email_confidence == "HIGH"


def test_pipeline_provider_failure_marks_job_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fail.db")
    monkeypatch.setenv("PDF_STORAGE_PATH", str(tmp_path / "reports"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models import database as db_mod

    db_mod.reset_engine()
    Base.metadata.create_all(db_mod.get_engine())

    from app.providers.base import PlaceSearchQuery, ProviderTimeout
    from app.providers.factory import get_provider

    class TimeoutProvider:
        name = "mock"

        def search_businesses(self, query: PlaceSearchQuery):
            raise ProviderTimeout("boom")

        def get_business_details(self, place_id, language="en"):
            return None

        def health_check(self):
            return True

    monkeypatch.setattr(
        "app.workers.pipeline.search_with_fallback",
        lambda *a, **kw: ([], "mock", ProviderTimeout("boom")),
    )

    with session_scope() as db:
        user = User(email="fail@user-test-example.com", hashed_password="x", role="USER")
        db.add(user)
        db.flush()
        job = SearchJob(user_id=user.id, query_raw="Hospitals somewhere",
                        parsed_category="Hospitals", parsed_location="Somewhere",
                        max_results=5, provider="mock")
        db.add(job)
        db.commit()
        job_id = job.id

    with session_scope() as db:
        run_pipeline(db, job_id)

    with session_scope() as db:
        job = db.get(SearchJob, job_id)
        assert job.status == "failed"
        assert "boom" in job.error_message
