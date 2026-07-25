"""Resolve the caller's identity from request headers.

Trust model:
  • Browser → IAP → Streamlit: IAP injects a *signed* JWT in
    ``X-Goog-IAP-JWT-Assertion``. We verify its signature + issuer (+ audience
    when configured) before trusting it. The plaintext
    ``X-Goog-Authenticated-User-Email`` header is NEVER trusted on its own —
    that would be the localStorage-blob mistake.
  • Streamlit → uvicorn (localhost): our own Streamlit process forwards the
    already-verified email in ``X-Aletheia-User``. uvicorn binds 127.0.0.1 only,
    so nothing external can set that header — it is trusted over the loopback
    boundary. (See deploy: the Cloud Run service is --no-allow-unauthenticated.)
  • Local dev (no IAP): ``ALETHEIA_DEV_USER`` / ``ALETHEIA_AUTH_DISABLED`` let
    ``docker compose up`` and bare uvicorn work without IAP.

Env is read live (not captured at import) so tests and per-request config work.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping, Optional

log = logging.getLogger(__name__)

IAP_JWT_HEADER = "x-goog-iap-jwt-assertion"
FORWARDED_USER_HEADER = "x-aletheia-user"  # set by our Streamlit AFTER verifying
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


@dataclass(frozen=True)
class Identity:
    email: str
    subject: Optional[str] = None
    verified: bool = False  # True only when sourced from a verified IAP JWT


def _header(headers: Optional[Mapping], name: str) -> Optional[str]:
    """Case-insensitive header lookup across Streamlit's ``st.context.headers``,
    Starlette ``Request.headers``, and plain dicts."""
    if not headers:
        return None
    try:
        get = headers.get
    except AttributeError:
        return None
    for key in (name, name.title(), name.upper(), name.replace("-", "_")):
        val = get(key)
        if val:
            return val
    return None


def _auth_disabled() -> bool:
    return os.environ.get("ALETHEIA_AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _dev_user() -> Optional[str]:
    u = os.environ.get("ALETHEIA_DEV_USER")
    return u.strip().lower() if u and u.strip() else None


def verify_iap_jwt(token: str) -> Optional[Identity]:
    """Verify an IAP assertion JWT. Returns a verified Identity or None."""
    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token
    except Exception as exc:  # pragma: no cover - dependency missing
        log.warning("google-auth unavailable; cannot verify IAP JWT: %s", exc)
        return None

    audience = os.environ.get("IAP_AUDIENCE") or None
    try:
        payload = id_token.verify_token(
            token,
            ga_requests.Request(),
            audience=audience,
            certs_url=IAP_CERTS_URL,
        )
    except Exception as exc:
        log.warning("IAP JWT verification failed: %s", exc)
        return None

    if payload.get("iss") != IAP_ISSUER:
        log.warning("IAP JWT unexpected issuer: %r", payload.get("iss"))
        return None
    email = (payload.get("email") or "").strip().lower()
    if not email:
        log.warning("IAP JWT missing email claim")
        return None
    if audience is None:
        log.warning(
            "IAP_AUDIENCE unset — verified signature+issuer only. Set IAP_AUDIENCE "
            "to the Cloud Run IAP audience for full verification."
        )
    return Identity(email=email, subject=payload.get("sub"), verified=True)


def current_identity(headers: Optional[Mapping] = None) -> Optional[Identity]:
    """Resolve the caller. Precedence: dev-bypass → verified IAP JWT →
    trusted forwarded header (loopback) → configured dev user → None."""
    if _auth_disabled():
        return Identity(email=_dev_user() or "dev@localhost", verified=False)

    jwt = _header(headers, IAP_JWT_HEADER)
    if jwt:
        ident = verify_iap_jwt(jwt)
        if ident:
            return ident
        # A present-but-invalid assertion is a hard fail — do not fall through.
        return None

    forwarded = _header(headers, FORWARDED_USER_HEADER)
    if forwarded:
        return Identity(email=forwarded.strip().lower(), verified=False)

    dev = _dev_user()
    if dev:
        return Identity(email=dev, verified=False)

    return None
