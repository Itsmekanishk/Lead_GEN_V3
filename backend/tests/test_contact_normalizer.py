"""Contact normalization tests (emails, phones)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.contact_normalizer import (  # noqa: E402
    extract_emails_from_text,
    extract_mailto_links,
    extract_phones_from_text,
    normalize_phone,
)


def test_extracts_valid_emails():
    findings = extract_emails_from_text(
        "Contact info@sunrisehospital.in or care@sunrisehospital.in",
        source_url="https://sunrisehospital.in/contact",
        business_domain="sunrisehospital.in",
    )
    emails = {f.email for f in findings}
    assert emails == {"info@sunrisehospital.in", "care@sunrisehospital.in"}


def test_high_confidence_for_business_domain():
    findings = extract_emails_from_text(
        "write to info@sunrisehospital.in",
        source_url="https://sunrisehospital.in",
        business_domain="sunrisehospital.in",
    )
    assert findings[0].confidence == "HIGH"


def test_invalid_email_rejected():
    findings = extract_emails_from_text(
        "not-an-email @broken no@domain", source_url=""
    )
    assert findings == []


def test_junk_emails_filtered():
    findings = extract_emails_from_text(
        "placeholder example.com test@example.org", source_url=""
    )
    assert findings == []


def test_obfuscated_email_repaired():
    findings = extract_emails_from_text(
        "info [at] sunrisehospital [dot] in", source_url="https://sunrisehospital.in",
        business_domain="sunrisehospital.in",
    )
    assert any(f.email == "info@sunrisehospital.in" for f in findings)


def test_mailto_extraction():
    findings = extract_mailto_links(
        '<a href="mailto:INFO@SunriseHospital.in?subject=hi">mail</a>',
        page_url="https://sunrisehospital.in",
        business_domain="sunrisehospital.in",
    )
    assert findings and findings[0].email == "info@sunrisehospital.in"


def test_indian_phone_normalization():
    assert normalize_phone("+91 98765 43210") == ("+919876543210", "+91")
    assert normalize_phone("09876543210") == ("+919876543210", "+91")
    assert normalize_phone("9876543210") == ("+919876543210", "+91")


def test_invalid_phone_returns_empty():
    assert normalize_phone("") == ("", "")
    assert normalize_phone("123") == ("", "")


def test_extract_phones_from_text():
    found = extract_phones_from_text("Call us at +91 98765 43210 today")
    assert found and "98765 43210" in found[0]
