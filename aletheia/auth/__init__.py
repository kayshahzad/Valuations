"""Authorization layer (Workstream 2).

Authentication is handled at the edge by Cloud Run IAP; this package turns the
IAP-verified identity into an in-app Admin/User role and enforces it.

  identity.current_identity(headers) -> Identity | None   # verify IAP JWT / dev
  rbac.role_for(email) -> "admin" | "user"
  deps.require_admin                                       # FastAPI dependency
"""
from .identity import Identity, current_identity, verify_iap_jwt
from .rbac import role_for, is_admin

__all__ = [
    "Identity",
    "current_identity",
    "verify_iap_jwt",
    "role_for",
    "is_admin",
]
