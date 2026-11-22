"""Contact discovery crawler for a business's own public website.

Strategy (bounded and polite):
1. Fetch homepage (robots.txt permitting).
2. From homepage links, pick candidate contact/about pages
   (Contact Us, About, footer links, mailto).
3. Fetch up to ``MAX_PAGES_PER_DOMAIN`` pages total, extracting emails
   (mailto + visible text) and phone numbers (tel: + text patterns).

Every page attempt is recorded in ``crawl_pages``; the crawler never raises
for per-page failures — a dead website cannot fail a search job.
"""
from __future__ import annotations

from typing import Optional

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.logging_config import event_logger
from app.crawlers.polite_fetcher import fetch_page, is_allowed_by_robots
from app.services.contact_normalizer import (
    ExtractedEmail,
    extract_emails_from_text,
    extract_mailto_links,
    extract_phones_from_text,
    normalize_phone,
)
from app.utils.normalization import domain_of

log = event_logger("locallead.crawler.contact")

_CONTACT_HINTS = (
    "contact", "contact-us", "contactus", "contact_us", "reach-us", "reach_us",
    "get-in-touch", "enquiry", "inquiry", "admission", "admissions", "connect",
    "support", "help", "locate", "location", "address", "branch", "branches"
)
_ABOUT_HINTS = ("about", "about-us", "aboutus", "about_us", "our-story", "team", "overview")
_NAV_SKIP_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com",
                   "linkedin.com", "youtube.com", "pinterest.com", "google.com", "goo.gl")


def _decode_cloudflare_email(cf_hex: str) -> str:
    """Decode Cloudflare email-protection obfuscated email address."""
    try:
        r = int(cf_hex[:2], 16)
        return "".join([chr(int(cf_hex[i:i + 2], 16) ^ r) for i in range(2, len(cf_hex), 2)])
    except Exception:
        return ""


@dataclass
class PageOutcome:
    """Record of a single crawled page (persisted to crawl_pages)."""

    url: str
    page_type: str
    status: str
    http_status: Optional[int] = None
    emails_found: int = 0
    phones_found: int = 0
    duration_ms: int = 0


@dataclass
class CrawlOutcome:
    """Aggregated result of crawling one business website."""

    start_url: str
    pages: list[PageOutcome] = field(default_factory=list)
    emails: list[ExtractedEmail] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    ok: bool = False
    summary: str = ""


def _same_site(base_url: str, link: str) -> bool:
    """True when *link* stays on the business's registrable domain."""
    base_domain = domain_of(urlparse(base_url).netloc)
    link_domain = domain_of(urlparse(link).netloc)
    return bool(link_domain) and link_domain == base_domain


def _extract_links(html_text: str, page_url: str) -> list[tuple[str, str]]:
    """Return (absolute_url, kind) links from HTML; kind in contact|about|other."""
    soup = BeautifulSoup(html_text or "", "lxml")
    out: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        text = anchor.get_text(" ", strip=True).lower()
        joined = urljoin(page_url, href)
        if joined.startswith("mailto:") or joined.startswith("tel:"):
            continue
        parsed = urlparse(joined)
        if parsed.scheme not in ("http", "https"):
            continue
        host = parsed.netloc.lower()
        if any(host.endswith(bad) or host == bad for bad in _NAV_SKIP_HOSTS):
            continue
        kind = "other"
        path = (parsed.path or "").lower()
        if any(h in path or h in text for h in _CONTACT_HINTS):
            kind = "contact"
        elif any(h in path or h in text for h in _ABOUT_HINTS):
            kind = "about"
        out.append((joined, kind))
    return out


def _visible_text(html_text: str) -> str:
    """Render HTML to text, dropping script/style content."""
    soup = BeautifulSoup(html_text or "", "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def crawl_business_website(start_url: str, business_domain: str = "",
                           max_pages: Optional[int] = None) -> CrawlOutcome:
    """Crawl a business website looking for public contact information.

    Never raises for network-level problems; all outcomes are captured in the
    returned CrawlOutcome (see also crawl_pages persistence in the pipeline).
    """
    settings = get_settings()
    max_pages = max_pages or settings.max_pages_per_domain
    outcome = CrawlOutcome(start_url=start_url)
    if not start_url:
        outcome.summary = "no website"
        return outcome

    domain = domain_of(urlparse(start_url).netloc) or business_domain
    if not domain:
        outcome.summary = "unparseable url"
        return outcome

    seen: set[str] = {start_url.rstrip("/")}
    queue: list[tuple[str, str]] = [(start_url, "home")]
    fetched = 0

    while queue and fetched < max_pages:
        url, page_type = queue.pop(0)
        if not is_allowed_by_robots(url):
            outcome.pages.append(PageOutcome(
                url=url, page_type=page_type, status="robots_disallowed",
            ))
            continue
        result = fetch_page(url)
        fetched += 1
        page_outcome = PageOutcome(
            url=result.final_url or url,
            page_type=page_type,
            status=result.status_kind,
            http_status=result.status,
            duration_ms=result.duration_ms,
        )
        if not result.ok:
            outcome.pages.append(page_outcome)
            continue

        outcome.ok = True
        current_page_url = result.final_url or url
        html_text = result.html
        text = _visible_text(html_text)

        emails = extract_mailto_links(html_text, page_url=current_page_url,
                                      business_domain=domain,
                                      check_disposable=settings.enable_disify_checks)
        emails += extract_emails_from_text(text, source_url=current_page_url,
                                           business_domain=domain,
                                           check_disposable=settings.enable_disify_checks)
        # Check for Cloudflare email obfuscation in HTML
        for cf_hex in re.findall(r"""(?:data-cfemail=["']|/cdn-cgi/l/email-protection#)([0-9a-fA-F]+)""", html_text):
            decoded_cf = _decode_cloudflare_email(cf_hex)
            if decoded_cf:
                emails += extract_emails_from_text(decoded_cf, source_url=current_page_url,
                                                   business_domain=domain,
                                                   check_disposable=settings.enable_disify_checks)

        # dedupe by email keeping the highest-confidence occurrence
        best: dict[str, ExtractedEmail] = {}
        for e in emails:
            if e.email not in best or e.confidence == "HIGH":
                best[e.email] = e
        for e in best.values():
            if e.email not in {x.email for x in outcome.emails}:
                outcome.emails.append(e)
        page_outcome.emails_found = len(best)

        phones = extract_phones_from_text(text, page_url=current_page_url)
        # Also extract tel: and WhatsApp click-to-chat numbers directly from html attributes
        for m in re.finditer(r"""(?:href=["']tel:|href=["']https?://(?:api\.)?whatsapp\.com/send\?phone=|href=["']https?://wa\.me/)([^"'?&#\s]+)""", html_text, re.I):
            val = m.group(1).strip()
            norm, _ = normalize_phone(val)
            if norm and val not in phones:
                phones.append(val)

        for raw in phones:
            norm, _cc = normalize_phone(raw)
            if norm and norm not in {normalize_phone(p)[0] for p in outcome.phones}:
                outcome.phones.append(raw)
        page_outcome.phones_found = len(phones)

        outcome.pages.append(page_outcome)

        if fetched < max_pages:
            for link, kind in _extract_links(html_text, current_page_url):
                normalized = link.rstrip("/")
                if normalized in seen or not (_same_site(start_url, link) or _same_site(current_page_url, link)):
                    continue
                if kind == "other":
                    continue
                seen.add(normalized)
                queue.append((normalized, kind))
                if len(queue) >= max_pages * 2:
                    break

    outcome.emails.sort(key=lambda e: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(e.confidence, 9))
    ok_pages = [p for p in outcome.pages if p.status == "ok"]
    outcome.summary = (
        f"{len(ok_pages)}/{len(outcome.pages)} pages ok, "
        f"{len(outcome.emails)} emails, {len(outcome.phones)} phones"
    )
    log.info(
        "website_scan_completed",
        domain=domain,
        pages=len(outcome.pages),
        emails_found=len(outcome.emails),
        phones_found=len(outcome.phones),
    )
    return outcome
