"""Reusable normalization helpers shared by pipeline stages."""
from __future__ import annotations

from typing import Optional

import re
from urllib.parse import urlparse, urlunparse

_NON_WORD = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

# Common legal suffixes stripped when comparing business names.
_NAME_SUFFIXES = (
    " pvt ltd", " private limited", " llc", " llp", " inc", " ltd",
    " co", " company", " corporation", " corp",
)


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip punctuation/suffixes/whitespace; used for dedup."""
    if not name:
        return ""
    s = _NON_WORD.sub(" ", name.lower())
    s = _WHITESPACE.sub(" ", s).strip()
    changed = True
    while changed and s:
        changed = False
        for suffix in _NAME_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                changed = True
    return s


def normalize_url(url: Optional[str]) -> str:
    """Return a canonical website URL: https, no www, no fragment, no query,
    no trailing slash. Empty string when unusable."""
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if not host or "." not in host:
        return ""
    return urlunparse(("https", host, path, "", "", ""))


def domain_of(url_or_host: Optional[str]) -> str:
    """Registrable-ish domain ('hospital.example.in' stays as-is; scheme/www stripped)."""
    if not url_or_host:
        return ""
    s = url_or_host.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    s = s.removeprefix("www.").split("/")[0].split(":")[0]
    return s


def normalize_address(address: Optional[str]) -> str:
    """Squash whitespace/punctuation for dedup comparisons."""
    if not address:
        return ""
    s = _NON_WORD.sub(" ", address.lower())
    return _WHITESPACE.sub(" ", s).strip()


def clean_category(category: Optional[str]) -> str:
    """Title-case a category string."""
    if not category:
        return ""
    return re.sub(r"\s+", " ", category.strip()).title()


def truncate(s: Optional[str], max_len: int) -> str:
    """Safe truncate for display/storage."""
    if not s:
        return ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"
