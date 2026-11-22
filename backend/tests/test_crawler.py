"""Crawler tests against a real local HTTP server (no external network)."""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402

PAGE = """
<html><body>
  <h1>Sunrise Hospital</h1>
  <a href="/contact-us">Contact us</a>
  <a href="/about-us">About</a>
  <a href="https://facebook.com/sunrise">Facebook</a>
  <a href="mailto:info@sunrisehospital.example">Email us</a>
  <p>Call +91 98765 43210.</p>
</body></html>
"""

CONTACT = """
<html><body>
  <h1>Contact Sunrise Hospital</h1>
  <p>Email: care@sunrisehospital.example or info@sunrisehospital.example</p>
  <p>Phone: 020 1234 5678</p>
</body></html>
"""

ROBOTS_ALLOW_EXCEPT_PRIVATE = "User-agent: *\nDisallow: /private\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body = ROBOTS_ALLOW_EXCEPT_PRIVATE.encode()
            self.send_response(200)
        elif path == "/":
            body = PAGE.encode()
            self.send_response(200)
        elif path.startswith("/contact-us"):
            body = CONTACT.encode()
            self.send_response(200)
        elif path.startswith("/about"):
            body = b"<html><body>About us</body></html>"
            self.send_response(200)
        else:
            body = b"<html><body>404</body></html>"
            self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture()
def crawl_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_WEBSITE_CRAWLING", "true")
    monkeypatch.setenv("RESPECT_ROBOTS_TXT", "true")
    monkeypatch.setenv("CRAWL_RATE_LIMIT_PER_DOMAIN_PER_SEC", "1000")
    # Keep SSRF protection ON; explicitly allow the loopback test server.
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1,localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_contact_discovery(server, crawl_enabled):
    from app.crawlers.contact_finder import crawl_business_website

    outcome = crawl_business_website(server + "/", max_pages=3)
    assert outcome.ok
    emails = {e.email for e in outcome.emails}
    assert "info@sunrisehospital.example" in emails
    assert "care@sunrisehospital.example" in emails
    types = {p.page_type for p in outcome.pages}
    assert "contact" in types


def test_robots_disallowed(server, crawl_enabled):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page(server + "/private/secret")
    assert result.status_kind == "robots_disallowed"


def test_dead_website_does_not_raise(crawl_enabled):
    from app.crawlers.contact_finder import crawl_business_website

    outcome = crawl_business_website("http://127.0.0.1:59999/", max_pages=2)
    assert not outcome.ok
    assert outcome.emails == []


def test_polite_fetcher_classifies_http_errors(server, crawl_enabled):
    from app.crawlers.polite_fetcher import fetch_page

    result = fetch_page(server + "/missing-page")
    assert result.status == 404
    assert result.status_kind == "http_error"
