# Authentication & Authorization — Architecture Comparison

**Question:** we need both authentication *and* authorization. Can IAP do this,
and how do the three reviewed approaches compare?

**Scope:** decision report comparing three auth models against Aletheia's needs
(Cloud Run deployment, FastAPI + Streamlit in one container, Gemini pipeline).
No code changed by this document.

**Date:** 2026-07-25.

---

## 1. The core distinction: authN vs authZ

These are two different problems and every approach below handles them differently.

- **Authentication (authN)** — *who are you?* Proving identity (password, Google
  sign-in, MFA).
- **Authorization (authZ)** — *what may you do?* Splits into two layers:
  - **Coarse authZ** — may you reach the app at all? (allow-list, context-aware
    conditions). Expressible at the edge / in IAM.
  - **Fine-grained authZ** — role- and tenant-scoped permissions inside the app
    ("Admin vs User", "org A can't read org B's data", "who can trigger a
    pipeline run"). **Always application logic** — no proxy or IAM policy can
    express it, because it depends on your data model.

**Bottom line on IAP:** it does authN fully and coarse authZ well. Fine-grained
authZ is never IAP's job — but IAP hands your app a *verified* identity to build
that authZ on, which is exactly what the other two approaches lack.

---

## 2. Can IAP do "both"?

| Capability | IAP alone | IAP + Identity Platform (GCIP) | Needs app code |
|---|---|---|---|
| Authenticate a Google account | ✅ | ✅ | — |
| Self-serve email/password signup, reset, MFA | ❌ | ✅ (Firebase Auth under the hood) | — |
| Edge enforcement (app unreachable until authed) | ✅ | ✅ | — |
| Coarse allow/deny per identity | ✅ (IAM) | ✅ | — |
| Context-aware access (device/IP/geo conditions) | ✅ (Access Context Mgr) | ✅ | — |
| Verified identity assertion to the app (signed JWT) | ✅ `X-Goog-IAP-JWT-Assertion` | ✅ + custom claims | verify it |
| Role/org RBAC, per-record scoping | ❌ | ❌ (claims help, but enforcement is yours) | ✅ **app** |

So: **IAP + a thin app-level authZ layer = both authN and authZ.** IAP does the
hard, security-critical authN + coarse authZ with zero app code; your app does
only the part that *must* be app code (roles, orgs), starting from an identity
you can trust.

---

## 3. The three approaches

- **A — Omnivo/Bluejay:** in-app custom email/password (Identity Toolkit REST),
  user blob in **`localStorage` (plaintext)**, `isAuthenticated` = blob present.
- **B — Streamlit + Firebase:** in-app gate (`show_auth_gate` → `st.stop()`),
  Firebase `sign_in_with_email_and_password`, user in **`st.session_state`** with
  **role + org_id**, plus signup / reset / invite.
- **C — IAP (+ optional Identity Platform):** Google auth at the **edge**; app
  unreachable until authenticated; IAM allow-list (+ optional GCIP for self-serve
  + custom claims); app enforces fine-grained authZ on the verified identity.

### Comparison matrix

| Dimension | A — localStorage | B — Firebase + session_state | C — IAP (+ GCIP) + app authZ |
|---|---|---|---|
| **Trust boundary** | In-app | In-app (the gate) | **Edge**, before the app |
| **Password handling** | Rolled by them ❌ | Firebase ✅ | Google / Firebase ✅ |
| **Session store** | Client localStorage, plaintext ❌ | Server-side `st.session_state` ✅ | Google session cookie + signed JWT ✅ |
| **Per-request revalidation** | None ❌ | Session-lifetime trust ⚠️ | **Every request at the proxy** ✅ |
| **Protects the FastAPI backend too** | n/a | **No** — Streamlit gate only ⚠️ | **Yes** — whole service ✅ |
| **Public attack surface pre-auth** | App exposed ❌ | App exposed ❌ | Edge absorbs it ✅ |
| **AuthN strength** | Weak | Good | **Strong** |
| **Coarse authZ** | Ad-hoc | Ad-hoc | **IAM + context-aware** ✅ |
| **Fine-grained authZ (role/org)** | None | **Yes** ✅ | App layer on verified identity ✅ |
| **Self-serve signup / invites** | Yes (unsafe) | **Yes** ✅ | Yes via **GCIP**; not via IAM allow-list |
| **App auth code to own/secure** | Lots (weakest link) | Moderate | **Minimal** (verify JWT + RBAC lookup) |
| **Portability** | Anywhere | Anywhere ✅ | GCP-coupled ⚠️ |
| **MFA** | No | Possible (Firebase) | **Yes** (GCIP) ✅ |

### Pros / cons

**A — localStorage.** Pro: none worth adopting. Con: plaintext client session, no
token, no revalidation, mock Google token, hardcoded/shared creds. *Reference for
what to avoid.*

**B — Firebase + `st.session_state`.** Pros: passwords in Firebase (not
hand-rolled); session is **server-side** (a real improvement over A); ships the
capability IAP lacks — **role + org_id + invite signup**; portable. Cons: the gate
is **code-level**, so safety depends on every entry path calling it; it protects
**only Streamlit, not the API**; **no mid-session revalidation** (a disabled/expired
account isn't noticed until re-login); the server is **publicly reachable before
auth**; on Cloud Run `session_state` is lost on scale-to-zero (→ re-login; needs
session affinity). It conflates authN and authZ in one hand-maintained layer.

**C — IAP (+ GCIP) + app authZ.** Pros: **edge enforcement** (smallest attack
surface, protects *both* processes), **zero hand-rolled authN**, per-request
verification, MFA + self-serve via GCIP, and a **verified identity** to key
fine-grained authZ on. Cons: fine-grained RBAC is still **your code** (IAP won't do
it); **GCP-coupled**; per-user identity in-app requires **verifying the IAP JWT**
(don't trust the plaintext `X-Goog-Authenticated-User-Email` header blindly).

---

## 4. Recommended architecture for Aletheia

Combine C's edge authN with B's authZ model — the standard "edge authN + app
authZ" split — so neither layer does the other's job:

```
                 Google sign-in / email+password (+ MFA)
 user ──HTTPS──►  IAP  ──(only if allow-listed)──►  Cloud Run: aletheia
   │  authN + coarse authZ + context-aware          │
   │  (optionally backed by Identity Platform for    │  X-Goog-IAP-JWT-Assertion
   │   self-serve signup, reset, MFA, custom claims) │  (verify signature!)
   ▼                                                 ▼
 Nothing reaches the app un-authenticated     app authZ layer:
                                               1. verify IAP JWT  → trusted email/sub
                                               2. look up role + org_id in a store
                                                  (Firestore / a table in DuckDB /
                                                   config) keyed on that identity
                                               3. enforce in FastAPI deps + Streamlit
                                                  (hide/deny by role; scope by org)
```

**Why this beats B standalone:** B's weak spots (public pre-auth surface, no
revalidation, backend uncovered, hand-rolled authN) are all handled by IAP, while
you keep B's genuinely useful part — the role/org model — as a *thin* authZ layer
over an identity you can actually trust.

**Why this beats C standalone:** C alone can't express "Admin vs User" or
multi-tenant scoping; the app authZ layer adds exactly that.

### What it takes to build (delta on the current deploy plan)

Small, and additive to the approved Cloud Run plan:

1. **Edge (no code):** enable IAP on the service (already in the plan). If you need
   self-serve signup / non-Google emails / MFA, enable **Identity Platform** and
   point IAP at it.
2. **Verify identity in-app:** a FastAPI dependency that validates the
   `X-Goog-IAP-JWT-Assertion` signature (against Google's public keys) and extracts
   `email`/`sub`. Reject if absent/invalid. *(This is the one security-critical bit
   of new code — small and well-documented.)*
3. **AuthZ store:** a `users` mapping `identity → {role, org_id}`. Options, cheapest
   first: a small config/JSON, a table in the existing DuckDB, or Firestore if you
   want a managed multi-tenant store with invites.
4. **Enforce:** decorate FastAPI routes with a role/org check dependency; in
   Streamlit, gate features/pages by the resolved role and filter data by `org_id`.
5. **Invites (only if self-serve):** GCIP supports this natively; otherwise an
   admin adds rows to the authZ store.

---

## 5. Decision guide

| If Aletheia is… | Use |
|---|---|
| **You + a few known people, one org** | **IAP allow-list only.** No app authZ needed yet — add role checks later if it grows. Current plan already covers this. |
| **Small team, needs Admin vs User** | **IAP + a lightweight app RBAC layer** (steps 2–4). No Identity Platform needed. |
| **Multi-tenant, self-serve signup, orgs, invites, MFA** | **IAP + Identity Platform + app RBAC/org** (all of §4). This is where B's whole feature set is genuinely required — but implemented on a secure edge instead of a public in-app gate. |

**Do not** adopt A's pattern in any scenario. **Do not** ship B *as-is* (public
Streamlit gate that leaves the API and the pre-auth surface exposed); if you want
B's features, put them behind IAP as in §4.

---

## 6. One open question that picks the row above

Is Aletheia meant to be an **internal tool** (you + known teammates) or a
**self-serve multi-tenant product** (users sign themselves up into orgs)? That
single answer decides whether we ship IAP-only, IAP + app RBAC, or IAP + Identity
Platform + app RBAC — and it's the only thing blocking a concrete implementation
plan for the authZ layer.
