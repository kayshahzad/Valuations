"""Auth layer: role mapping, identity resolution, and the write-method guard."""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from aletheia.auth import rbac
from aletheia.auth.identity import Identity, current_identity
from aletheia.auth.deps import require_admin


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start each test from a known auth env."""
    for var in ("ADMIN_EMAILS", "ALETHEIA_DEV_USER", "ALETHEIA_AUTH_DISABLED", "IAP_AUDIENCE"):
        monkeypatch.delenv(var, raising=False)
    yield


# ── rbac.role_for ────────────────────────────────────────────────────────────
def test_role_for_admin_case_insensitive(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@X.com, admin2@x.com")
    assert rbac.role_for("boss@x.com") == "admin"
    assert rbac.role_for("BOSS@X.COM") == "admin"
    assert rbac.role_for("admin2@x.com") == "admin"


def test_role_for_non_admin_and_none(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@x.com")
    assert rbac.role_for("someone@x.com") == "user"
    assert rbac.role_for(None) == "user"
    assert rbac.role_for("") == "user"


def test_role_for_empty_admin_list_all_users():
    assert rbac.role_for("anyone@x.com") == "user"


# ── identity resolution ──────────────────────────────────────────────────────
def test_forwarded_header_identity():
    ident = current_identity({"X-Aletheia-User": "Boss@X.com"})
    assert ident == Identity(email="boss@x.com", subject=None, verified=False)


def test_dev_user_fallback(monkeypatch):
    monkeypatch.setenv("ALETHEIA_DEV_USER", "dev@x.com")
    ident = current_identity(None)
    assert ident and ident.email == "dev@x.com" and not ident.verified


def test_auth_disabled_bypass(monkeypatch):
    monkeypatch.setenv("ALETHEIA_AUTH_DISABLED", "true")
    ident = current_identity(None)
    assert ident and ident.email == "dev@localhost"


def test_no_identity_returns_none():
    assert current_identity(None) is None
    assert current_identity({}) is None


def test_invalid_iap_jwt_hard_fails():
    # A present-but-invalid assertion must NOT fall through to weaker sources.
    assert current_identity({"x-goog-iap-jwt-assertion": "garbage.token"}) is None


# ── require_admin dependency ─────────────────────────────────────────────────
def _client():
    app = FastAPI()

    @app.post("/admin-only")
    def _endpoint(ident: Identity = Depends(require_admin)):
        return {"email": ident.email}

    return TestClient(app, raise_server_exceptions=True)


def test_require_admin_allows_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@x.com")
    r = _client().post("/admin-only", headers={"X-Aletheia-User": "boss@x.com"})
    assert r.status_code == 200 and r.json()["email"] == "boss@x.com"


def test_require_admin_forbids_user(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@x.com")
    r = _client().post("/admin-only", headers={"X-Aletheia-User": "rando@x.com"})
    assert r.status_code == 403


def test_require_admin_unauthenticated():
    r = _client().post("/admin-only")
    assert r.status_code == 401


# ── real api_main middleware (write-method guard) ────────────────────────────
def test_api_write_methods_require_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@x.com")
    import api_main

    client = TestClient(api_main.app)
    # No identity on a write → blocked before the handler runs.
    assert client.post("/pipeline/bust-cache/AAPL").status_code in (401, 403)
    # Non-admin identity on a write → 403.
    assert client.post(
        "/pipeline/bust-cache/AAPL", headers={"X-Aletheia-User": "rando@x.com"}
    ).status_code == 403
    # A GET is not gated by the write guard (unknown path → 404, not 401/403).
    assert client.get("/definitely-not-a-route").status_code == 404
