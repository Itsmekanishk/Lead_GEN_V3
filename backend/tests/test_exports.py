"""Export generation tests (PDF, CSV, XLSX)."""
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.exports.csv_export import build_csv  # noqa: E402
from app.exports.pdf_report import build_pdf_report  # noqa: E402
from app.exports.xlsx_export import build_xlsx  # noqa: E402
from app.models.models import Business  # noqa: E402
from app.utils.datetime_utils import utcnow  # noqa: E402

COLUMNS = [
    "Business Name", "Category", "Address", "Phone", "Email", "Website",
    "Rating", "Review Count", "Email Confidence", "Phone Source",
    "Website Source", "Search Query", "Search Location", "Collected At",
]


def _biz(i: int) -> Business:
    return Business(
        job_id="job", provider="mock", provider_place_id=f"p{i}",
        name=f"Business {i}", category="Hospitals", address=f"{i} Road, Pune",
        phone="+91 98765 43210", phone_normalized="+919876543210",
        phone_source="provider",
        email=f"info{i}@example.com" if i % 2 else "",
        email_confidence="HIGH" if i % 2 else "",
        website=f"https://www.example{i}.com", website_normalized=f"example{i}.com",
        rating=4.0 + (i % 5) / 10, review_count=10 + i, source="mock provider",
        created_at=utcnow(), updated_at=utcnow(),
    )


def test_pdf_with_one_result(tmp_path):
    out = tmp_path / f"report_{uuid.uuid4()}.pdf"
    build_pdf_report(out, search_query="Hospitals in Pune", category="Hospitals",
                     location="Pune", businesses=[_biz(1)], provider="mock")
    assert out.exists() and out.stat().st_size > 1000
    assert out.read_bytes()[:4] == b"%PDF"


def test_pdf_with_100_results(tmp_path):
    out = tmp_path / f"report_{uuid.uuid4()}.pdf"
    build_pdf_report(out, search_query="Hospitals in Pune", category="Hospitals",
                     location="Pune", businesses=[_biz(i) for i in range(120)],
                     provider="mock")
    assert out.exists() and out.stat().st_size > 5000


def test_csv_columns_and_content(tmp_path):
    out = tmp_path / "export.csv"
    rows = [{
        "Business Name": b.name, "Category": b.category, "Address": b.address,
        "Phone": b.phone_normalized, "Email": b.email, "Website": b.website,
        "Rating": b.rating, "Review Count": b.review_count,
        "Email Confidence": b.email_confidence, "Phone Source": b.phone_source,
        "Website Source": b.source, "Search Query": "q",
        "Search Location": "Pune", "Collected At": b.updated_at,
    } for b in [_biz(1), _biz(2)]]
    build_csv(out, rows)
    with open(out, encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        assert header == COLUMNS
        body = list(reader)
    assert len(body) == 2
    assert body[0][COLUMNS.index("Email Confidence")] == "HIGH"


def test_xlsx_contains_rows(tmp_path):
    out = tmp_path / "export.xlsx"
    rows = [{
        "Business Name": b.name, "Category": b.category, "Address": b.address,
        "Phone": b.phone_normalized, "Email": b.email, "Website": b.website,
        "Rating": b.rating, "Review Count": b.review_count,
        "Email Confidence": b.email_confidence, "Phone Source": b.phone_source,
        "Website Source": b.source, "Search Query": "q",
        "Search Location": "Pune", "Collected At": b.updated_at,
    } for b in [_biz(3)]]
    build_xlsx(out, rows)
    assert out.exists() and out.stat().st_size > 1000

    from openpyxl import load_workbook

    wb = load_workbook(out)
    ws = wb.active
    assert ws.cell(row=1, column=1).value == "Business Name"
    assert ws.cell(row=2, column=1).value == "Business 3"


# ---------------------------------------------------------------------------
# Phase 14/15/48 additions: Unicode, injection, missing fields, scale
# ---------------------------------------------------------------------------


def _row(**overrides) -> dict:
    b = _biz(9)
    row = {
        "Business Name": b.name, "Category": b.category, "Address": b.address,
        "Phone": b.phone_normalized, "Email": b.email, "Website": b.website,
        "Rating": b.rating, "Review Count": b.review_count,
        "Email Confidence": b.email_confidence, "Phone Source": b.phone_source,
        "Website Source": b.source, "Search Query": "q",
        "Search Location": "Pune", "Collected At": b.updated_at,
    }
    row.update(overrides)
    return row


def test_csv_unicode_and_quoting(tmp_path):
    """Indian names, commas, quotes and newlines must round-trip exactly."""
    out = tmp_path / "unicode.csv"
    name = 'सूर्य अस्पताल, "पुणे" — Sunrise\nMulti-line'
    build_csv(out, [_row(**{"Business Name": name, "Address": "पोरवाल रोड, पुणे 411045"})])
    with open(out, encoding="utf-8-sig") as fh:
        body = list(csv.DictReader(fh))
    assert body[0]["Business Name"] == name
    assert body[0]["Address"] == "पोरवाल रोड, पुणे 411045"


def test_csv_formula_injection_sanitized(tmp_path):
    """Spreadsheet-formula prefixes must be neutralized in every column."""
    out = tmp_path / "inject.csv"
    build_csv(out, [_row(**{"Business Name": "=HYPERLINK(\"http://evil\",\"x\")",
                            "Email": "+CMD|' /C calc'!A0"})])
    with open(out, encoding="utf-8-sig") as fh:
        body = list(csv.DictReader(fh))
    assert body[0]["Business Name"].startswith("'=HYPERLINK")
    assert body[0]["Email"].startswith("'+CMD")


def test_xlsx_formula_injection_stored_as_text(tmp_path):
    out = tmp_path / "inject.xlsx"
    build_xlsx(out, [_row(**{"Business Name": "=1+1"})])

    from openpyxl import load_workbook

    ws = load_workbook(out).active
    assert ws.cell(row=2, column=1).value == "'=1+1"


def test_xlsx_unicode_and_long_names(tmp_path):
    out = tmp_path / "unicode.xlsx"
    long_name = "श्री सूर्य मल्टीस्पेशालिटी हॉस्पिटल अँड रिसर्च सेंटर प्रा. लि." * 2
    build_xlsx(out, [_row(**{"Business Name": long_name,
                             "Address": "Shop 4, विमाननगर, पुणे"})])

    from openpyxl import load_workbook

    ws = load_workbook(out).active
    assert ws.cell(row=2, column=1).value == long_name


def test_pdf_with_missing_fields_does_not_crash(tmp_path):
    """One business with every optional field empty must not break the PDF."""
    bare = Business(
        job_id="job", provider="mock", provider_place_id="px", name=None,
        category="", address=None, phone=None, email=None, website=None,
    )
    out = tmp_path / f"report_{uuid.uuid4()}.pdf"
    build_pdf_report(out, search_query="Edge case", category="",
                     location="", businesses=[bare], provider="mock")
    assert out.exists() and out.read_bytes()[:4] == b"%PDF"


def test_pdf_unicode_content(tmp_path):
    out = tmp_path / f"report_{uuid.uuid4()}.pdf"
    biz = _biz(1)
    biz.name = "सूर्य अस्पताल & Sons <Ltd>"
    biz.address = "पोरवाल रोड, पुणे — 411045"
    build_pdf_report(out, search_query="Hospitals in Porwal Road Pune",
                     category="Hospitals", location="Porwal Road, Pune",
                     businesses=[biz], provider="mock")
    assert out.exists() and out.stat().st_size > 1000


def test_pdf_with_500_results(tmp_path):
    out = tmp_path / f"report_{uuid.uuid4()}.pdf"
    build_pdf_report(out, search_query="Hospitals in Pune", category="Hospitals",
                     location="Pune", businesses=[_biz(i) for i in range(500)],
                     provider="mock")
    assert out.exists() and out.stat().st_size > 20000
    assert out.read_bytes()[:4] == b"%PDF"


def test_xlsx_with_500_rows(tmp_path):
    out = tmp_path / "large.xlsx"
    build_xlsx(out, [_row(**{"Business Name": f"Business {i}"}) for i in range(500)])

    from openpyxl import load_workbook

    ws = load_workbook(out, read_only=True).active
    assert ws.max_row == 501  # header + 500
