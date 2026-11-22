"""Contact extraction & normalization: emails and phone numbers.

Emails:
- Extracted from mailto: links and visible text only — never synthesized.
- Syntax-validated, lowercased, de-duplicated.
- Confidence: HIGH = official business domain, MEDIUM = official page but
  third-party mail host, LOW = found on a different domain than the business.

Phones:
- Provider data is primary; website numbers enrich only.
- Indian numbers normalized to E.164 (+91XXXXXXXXXX); other countries pass
  through best-effort with the raw value preserved.
"""
from __future__ import annotations

from typing import Optional

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

from app.utils.normalization import domain_of

# --------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)
# heuristics to drop false positives commonly found in page source
_JUNK_EMAIL_HINTS = (
    "example.com", "example.org", "sentry.io", "wixpress.com",
    "@2x.png", ".jpg", ".png", ".webp", ".gif", ".svg",
)
_FREE_MAIL_HOSTS = {
    "gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "hotmail.com",
    "outlook.com", "live.com", "rediffmail.com", "icloud.com", "aol.com",
    "protonmail.com", "mail.com", "yandex.com",
}

# obfuscation repairs: \"info [at] hospital dot com\" -> info@hospital.com
_OBFUSCATED = [
    (re.compile(r"\s*\[\s*at\s*\]\s*", re.I), "@"),
    (re.compile(r"\s*\(\s*at\s*\)\s*", re.I), "@"),
    (re.compile(r"\s+at\s+", re.I), "@"),
    (re.compile(r"\s*\[\s*dot\s*\]\s*", re.I), "."),
    (re.compile(r"\s*\(\s*dot\s*\)\s*", re.I), "."),
    (re.compile(r"\s+dot\s+", re.I), "."),
]


@dataclass
class ExtractedEmail:
    """A single email finding with provenance."""

    email: str
    source_url: str
    confidence: str  # HIGH | MEDIUM | LOW
    context: str = ""
    # Disify verdict (True = disposable/temp host, False = not disposable,
    # None = unknown / check disabled).
    disposable: Optional[bool] = None


def _deobfuscate(text: str) -> str:
    for pattern, replacement in _OBFUSCATED:
        text = pattern.sub(replacement, text)
    return text


def extract_emails_from_text(text: str, source_url: str = "",
                             business_domain: str = "",
                             check_disposable: bool = False) -> list[ExtractedEmail]:
    """Extract unique, syntactically-valid emails from raw page text/HTML.

    When *check_disposable* is true, each unique address is also checked
    against Disify (free, keyless API); disposable addresses are downgraded
    to LOW confidence. The verdict is stored on the finding so the pipeline
    can persist it (``BusinessContact.email_disposable``).
    """
    if not text:
        return []
    decoded = html.unescape(text)
    decoded = _deobfuscate(decoded)
    found: dict[str, ExtractedEmail] = {}
    for match in _EMAIL_RE.finditer(decoded):
        email = match.group(0).strip(".,;:<>\"'").lower()
        if not email or "@" not in email:
            continue
        local, _, domain = email.partition("@")
        if not local or not domain or "." not in domain:
            continue
        if any(hint in email for hint in _JUNK_EMAIL_HINTS):
            continue
        if domain.startswith(".") or domain.endswith("."):
            continue
        if email in found:
            continue
        if business_domain and domain_of(domain) == domain_of(business_domain):
            confidence = "HIGH"
        elif domain in _FREE_MAIL_HOSTS and business_domain:
            confidence = "MEDIUM"
        elif business_domain:
            confidence = "LOW"
        else:
            confidence = "MEDIUM"
        disposable: Optional[bool] = None
        if check_disposable and confidence != "HIGH":
            disposable = _disify_disposable(email)
            if disposable is True:
                # throwaway hosts (mailinator & co) are never a real contact
                confidence = "LOW"
        # APILayer mailboxlayer enrichment (keyless no-op when unset):
        # MX/SMTP-verified verdicts downgrade undeliverable or throwaway
        # addresses; HIGH-confidence emails are only checked when explicitly
        # enabled via MAILBOXLAYER_CHECK_HIGH_CONFIDENCE.
        if check_disposable and (confidence != "HIGH"
                                 or _mailboxlayer_high_conf_enabled()):
            verdict = _mailboxlayer_validate(email)
            if verdict is not None:
                if verdict.disposable:
                    disposable = True
                    confidence = "LOW"
                if verdict.mx_found is False or verdict.smtp_check is False:
                    # mailbox exists in neither DNS nor SMTP — not contactable
                    confidence = "LOW"
        found[email] = ExtractedEmail(
            email=email,
            source_url=source_url,
            confidence=confidence,
            context=decoded[max(0, match.start() - 40): match.end() + 40],
            disposable=disposable,
        )
    return list(found.values())


_DISIFY_CACHE: dict[str, Optional[bool]] = {}


def _mailboxlayer_high_conf_enabled() -> bool:
    """Mailboxlayer also verifies HIGH-confidence emails when enabled."""
    try:
        from app.core.config import get_settings

        return bool(get_settings().mailboxlayer_check_high_confidence)
    except Exception:  # noqa: BLE001 — settings problems disable enrichment
        return False


def _mailboxlayer_validate(email: str):
    """Thin wrapper so tests can patch; returns None when not configured."""
    try:
        from app.services.apilayer import validate_email

        return validate_email(email)
    except Exception:  # noqa: BLE001 — enrichment must never break extraction
        return None


def _disify_disposable(email: str) -> Optional[bool]:
    """Query Disify for a disposable verdict (cached; None = unknown).

    Never raises: on any network/HTTP failure the verdict is None (unknown)
    and the local heuristics keep their original confidence tier.
    """
    if email in _DISIFY_CACHE:
        return _DISIFY_CACHE[email]
    verdict: Optional[bool] = None
    try:
        import httpx

        resp = httpx.get(f"https://disify.com/api/email/{email}",
                         timeout=6.0)
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict) and "disposable" in payload:
                verdict = bool(payload["disposable"])
    except Exception:  # noqa: BLE001 — unreachable service must not fail crawling
        verdict = None
    _DISIFY_CACHE[email] = verdict
    return verdict


def extract_mailto_links(html_text: str, page_url: str,
                         business_domain: str = "",
                         check_disposable: bool = False) -> list[ExtractedEmail]:
    """Pull email addresses from mailto: anchors in HTML."""
    out: list[ExtractedEmail] = []
    for match in re.finditer(
        r"href=[\"']mailto:([^\"'?]+)", html_text or "", re.I
    ):
        raw = unquote(match.group(1)).strip()
        email = raw.split(",")[0].strip().lower()
        email_obj = extract_emails_from_text(email, source_url=page_url,
                                             business_domain=business_domain,
                                             check_disposable=check_disposable)
        out.extend(email_obj)
    return out


def best_email(findings: list[ExtractedEmail]) -> Optional[ExtractedEmail]:
    """Pick the highest-confidence email; ties broken by domain authority."""
    if not findings:
        return None
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(findings, key=lambda f: (order.get(f.confidence, 9), f.email))[0]


# --------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------

_TEL_RE = re.compile(r"tel:\+?\d[\d\s\-\(\)]{6,}\d", re.I)
_WHATSAPP_RE = re.compile(
    r"""(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp://send\?phone=)[/]?(\+?\d{10,15})""",
    re.I,
)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-\(\)\.]{7,16}\d)")


def _digits_only(raw: str) -> str:
    return re.sub(r"[^\d]", "", raw)


def normalize_phone(raw: Optional[str], default_country: str = "IN") -> tuple[str, str]:
    """Return (normalized_e164_or_empty, country_code).

    Uses the ``phonenumbers`` library when available for correct Indian
    normalization; falls back to a conservative regex-based rule set.
    """
    if not raw:
        return "", ""
    raw = raw.strip()
    try:
        import phonenumbers  # type: ignore

        parsed = phonenumbers.parse(raw, default_country or "IN")
        if phonenumbers.is_valid_number(parsed):
            return (
                f"+{parsed.country_code}{parsed.national_number}",
                f"+{parsed.country_code}",
            )
        return "", ""
    except ImportError:
        pass
    except Exception:
        return "", ""

    # ---- regex fallback (no phonenumbers installed) ----------------------
    digits = _digits_only(raw)
    if not digits:
        return "", ""
    if digits.startswith("00"):
        digits = digits[2:]
    if raw.strip().startswith("+") or digits.startswith("91"):
        if len(digits) == 12 and digits.startswith("91"):
            return f"+{digits}", "+91"
        if len(digits) == 10:
            return f"+91{digits}", "+91"
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}", "+91"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}", "+91"
    return "", ""


def extract_phones_from_text(text: str, page_url: str = "") -> list[str]:
    """Extract plausible phone numbers from visible text, tel: links, and WhatsApp links."""
    out: list[str] = []
    if not text:
        return out
    # 1. WhatsApp click-to-chat links
    for m in _WHATSAPP_RE.finditer(text):
        num = m.group(1).strip()
        normalized, _cc = normalize_phone(num)
        if normalized:
            out.append(normalized)
    # 2. tel: links
    for m in _TEL_RE.finditer(text):
        cand = m.group(0)[4:].strip()
        normalized, _cc = normalize_phone(cand)
        if normalized:
            out.append(cand)
    # 3. Visible text patterns
    stripped = _TEL_RE.sub(" ", text)
    stripped = _WHATSAPP_RE.sub(" ", stripped)
    for m in _PHONE_RE.finditer(stripped):
        candidate = m.group(0)
        digits = _digits_only(candidate)
        if 10 <= len(digits) <= 13:
            normalized, _cc = normalize_phone(candidate)
            if normalized:
                out.append(candidate)
    # dedupe by digits
    seen: set[str] = set()
    unique: list[str] = []
    for cand in out:
        key = _digits_only(cand)
        if key and key not in seen:
            seen.add(key)
            unique.append(cand.strip())
    return unique
