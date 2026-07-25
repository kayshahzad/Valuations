"""FastAPI dependencies for the mutating/admin endpoints (defense in depth).

The primary enforcement point is Streamlit (the only externally reachable
surface); uvicorn is localhost-only inside the container. These dependencies
re-check on the backend so an admin-only route can never be driven by a
non-admin, even from within the container.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from .identity import Identity, current_identity
from .rbac import role_for


def current_user(request: Request) -> Identity:
    ident = current_identity(request.headers)
    if ident is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return ident


def require_admin(request: Request) -> Identity:
    ident = current_user(request)
    if role_for(ident.email) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return ident
