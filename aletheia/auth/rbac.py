"""Role mapping: an identity is an Admin iff its email is in ``ADMIN_EMAILS``
(comma-separated, case-insensitive). Everyone else authenticated is a User.

Env is read live so a redeploy that changes ADMIN_EMAILS takes effect without
code changes, and tests can monkeypatch the environment.
"""
from __future__ import annotations

import os
from typing import Optional, Set

ADMIN = "admin"
USER = "user"


def admin_emails() -> Set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email: Optional[str]) -> bool:
    if not email:
        return False
    return email.strip().lower() in admin_emails()


def role_for(email: Optional[str]) -> str:
    return ADMIN if is_admin(email) else USER
