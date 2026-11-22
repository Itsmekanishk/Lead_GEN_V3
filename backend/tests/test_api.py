"""API integration tests: auth, RBAC, search lifecycle, admin."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def inline_dispatch(monkeypatch):
    """Run search jobs inline (no Redis/Celery) inside API tests."""
    from app.workers import pipeline as pipeline_mod

    monkeypatch.setattr(
        "app.workers.dispatch.dispatch_search_job",
        lambda job_id: _run_inline(pipeline_mod, job_id),
    )


def _run_inline(pipeline_mod, job_id):
    from app.models.database import session_scope

    with session_scope() as db:
        pipeline_mod.run_pipeline(db, job_id)


# --------------------------------------------------------------------------- 
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "database" in body["checks"]
    assert "provider" in body["checks"]


# --------------------------------------------------------------------------- 
# Auth
# ---------------------------------------------------------------------------


def test_register_login_me(client):
    r = client.post("/api/auth/register", json={
        "email": "alice@user-test-example.com", "password": "password123",
        "full_name": "Alice",
    })
    assert r.status_code == 201
    tokens = r.json()
    assert tokens["access_token"]

    r2 = client.post("/api/auth/login", json={
        "email": "alice@user-test-example.com", "password": "password123",
    })
    assert r2.status_code == 200

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    r3 = client.get("/api/auth/me", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["email"] == "alice@user-test-example.com"


def test_login_wrong_password(client, user_tokens):
    r = client.post("/api/auth/login", json={
        "email": "user@user-test-example.com", "password": "wrongpassword",
    })
    assert r.status_code == 401


def test_me_requires_auth(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_password_reset_flow(client, user_tokens):
    r = client.post("/api/auth/forgot-password", json={
        "email": "user@user-test-example.com"})
    assert r.status_code == 200
    token = r.json().get("dev_token")
    assert token  # non-production environment returns the token for testing
    r2 = client.post("/api/auth/reset-password", json={
        "token": token, "new_password": "newpassword123",
    })
    assert r2.status_code == 200
    r3 = client.post("/api/auth/login", json={
        "email": "user@user-test-example.com", "password": "newpassword123",
    })
    assert r3.status_code == 200


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={
        "email": "short@user-test-example.com", "password": "short",
    })
    assert r.status_code in (400, 422)


# --------------------------------------------------------------------------- 
# Search lifecycle (mock provider; crawling disabled in conftest)
# ---------------------------------------------------------------------------


def test_interpret_endpoint(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search/interpret", json={
        "free_text": "Hospitals in Porwal Road Pune",
    }, headers=headers)
    assert r.status_code == 200
    interpreted = r.json()["interpreted"]
    assert interpreted["category"] == "Hospitals"
    assert "Porwal Road" in (interpreted["location"] or "")


def test_create_search_runs_inline_and_completes(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 10,
    }, headers=headers)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = client.get(f"/api/search/{job_id}", headers=headers).json()
    assert status["status"] == "completed"
    assert status["progress_stats"]["total"] == 10

    results = client.get(f"/api/search/{job_id}/results", headers=headers).json()
    assert results["total"] == 10
    assert all(item["name"] for item in results["items"])


def test_create_search_requires_query(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={"max_results": 10}, headers=headers)
    assert r.status_code == 422


def test_empty_results_scenario(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "empty hospitals somewhere", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    results = client.get(f"/api/search/{job_id}/results", headers=headers).json()
    assert results["total"] == 0
    summary = client.get(f"/api/search/{job_id}/summary", headers=headers).json()
    assert summary["summary"]["total"] == 0


def test_job_ownership_enforced(client, user_tokens, admin_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Aundh Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    # admin may view (RBAC: admin sees all jobs)
    r2 = client.get(f"/api/search/{job_id}", headers=admin_tokens[0])
    assert r2.status_code == 200
    # anonymous may not
    r3 = client.get(f"/api/search/{job_id}")
    assert r3.status_code == 401


def test_search_validation_rejects_bad_provider(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Pune", "provider": "hax",
    }, headers=headers)
    assert r.status_code == 422


def test_rate_limit_blocks_flood(client, user_tokens, monkeypatch):
    headers = user_tokens[0]
    monkeypatch.setenv("LOCALLEAD_TEST_SEARCH_LIMIT", "2")
    for _ in range(2):
        r = client.post("/api/search", json={
            "free_text": "empty fields", "max_results": 3}, headers=headers)
        assert r.status_code == 202
    r3 = client.post("/api/search", json={
        "free_text": "empty fields", "max_results": 3}, headers=headers)
    assert r3.status_code == 429


# --------------------------------------------------------------------------- 
# RBAC
# ---------------------------------------------------------------------------


def test_admin_endpoints_require_admin(client, user_tokens):
    headers = user_tokens[0]
    for path in ("/api/admin/jobs", "/api/admin/users", "/api/admin/usage"):
        r = client.get(path, headers=headers)
        assert r.status_code == 403


def test_admin_endpoints_work_for_admin(client, admin_tokens):
    headers = admin_tokens[0]
    assert client.get("/api/admin/jobs", headers=headers).status_code == 200
    assert client.get("/api/admin/users", headers=headers).status_code == 200
    usage = client.get("/api/admin/usage", headers=headers)
    assert usage.status_code == 200
    assert "today_requests" in usage.json()


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------------------
# Search deletion (history page "delete" action)
# ---------------------------------------------------------------------------


def test_delete_completed_search(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Porwal Road Pune", "max_results": 5,
    }, headers=headers)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    dr = client.delete(f"/api/search/{job_id}", headers=headers)
    assert dr.status_code == 204
    # job and its results are gone afterwards
    assert client.get(f"/api/search/{job_id}", headers=headers).status_code == 404
    assert client.get(f"/api/search/{job_id}/results", headers=headers).status_code == 404


def test_delete_running_search_conflict(client, user_tokens, monkeypatch):
    headers = user_tokens[0]
    # keep the job queued: override the inline dispatcher for this test
    monkeypatch.setattr("app.workers.dispatch.dispatch_search_job", lambda job_id: None)
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Kharadi Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]

    dr = client.delete(f"/api/search/{job_id}", headers=headers)
    assert dr.status_code == 409
    # the queued job is untouched
    assert client.get(f"/api/search/{job_id}", headers=headers).status_code == 200


def test_delete_requires_auth(client, user_tokens):
    headers = user_tokens[0]
    r = client.post("/api/search", json={
        "free_text": "Hospitals in Pune", "max_results": 5,
    }, headers=headers)
    job_id = r.json()["job_id"]
    assert client.delete(f"/api/search/{job_id}").status_code == 401
    assert r.headers.get("x-frame-options") == "DENY"
