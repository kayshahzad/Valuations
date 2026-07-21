"""Management roster extraction (Qualitative tab, §management) — v1.

Reference-data layer: the names, roles, tenure, and career history of the
executive team AND the board of directors — display-only, NOT a scored
judgment (per locked decision) and NOT part of the HITL confirm flow.

Sources, in priority order:
  1. **DEF 14A proxy** — primary for director + officer bios/career
     history (already fetched by the librarian → ``raw_def14a_text``).
  2. **10-K Part I** — "Information about our Executive Officers" (SEC
     General Instruction G(3)): name/age/title/short history for
     EXECUTIVE OFFICERS. Passed in when the librarian captures it.
  3. **FMP key-executives** — a structured name/title roster used ONLY to
     cross-check extracted names (hallucination guard); no bios.

Foreign private issuers (ASML/TSM/NVO — 20-F filers) have NO DEF 14A and
often no Part I officer block; those degrade to an FMP-only name/title
skeleton with an explicit "bios unavailable" note (handled by the caller/
renderer), never a silently-empty panel.

Evidence discipline mirrors the bottom-up layer (``business_extraction``):
every bio / career-history claim must carry a VERBATIM filing quote tagged
``roster:{name}:{field}``. Unsupportable claims are OMITTED, not stated —
fabricating a real person's credentials is a genuine harm, so the read
path flags any unsourced member and cross-checks names against FMP.

Cost discipline: one structured LLM call, run inside the Stage-4
extraction node where the filing text is already loaded. Cache to disk
(annual TTL). Returns ``None`` (never raises) when no API key, no filing
text, or parse fails — the panel simply doesn't render.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Reuse the bottom-up evidence model (claim / verbatim quote / source).
from aletheia.agents.business_extraction import BusinessEvidence

_CACHE_DIR = Path("valuation_data/macro/management_roster")
_TTL_SECONDS = 370 * 24 * 3600  # ~1 year (proxy/10-K cadence)
_EVIDENCE_SCHEMA_VERSION = 1


# ── Schema ──────────────────────────────────────────────────────────────────

class RosterMember(BaseModel):
    name: str = Field(description="Full name of the executive or director.")
    role: str = Field(
        description="Current position: 'CEO', 'CFO', 'COO', 'Chair', "
                    "'Lead Independent Director', 'Director', etc.")
    member_type: str = Field(
        default="",
        description="'executive', 'director', or 'both' (an executive who "
                    "also sits on the board, e.g. an exec chair).")
    age: Optional[int] = Field(default=None, description="Age if disclosed.")
    tenure_years: Optional[int] = Field(
        default=None,
        description="Years in the current role / on the board, if derivable.")
    since_year: Optional[int] = Field(
        default=None, description="Year they took the role / joined the board.")
    bio_summary: str = Field(
        default="",
        description="2-3 sentence career-history summary from the filing. "
                    "REQUIRES a supporting evidence quote. Omit if unsupported.")
    prior_roles: List[str] = Field(
        default_factory=list,
        description="Prior positions/companies as stated in the filing. Each "
                    "material claim needs an evidence quote.")
    committees: List[str] = Field(
        default_factory=list,
        description="Board committee memberships (directors): Audit, "
                    "Compensation, Nominating/Governance, Risk, etc.")
    other_public_boards: List[str] = Field(
        default_factory=list,
        description="Other public-company boards they serve on, if disclosed.")


class ManagementRoster(BaseModel):
    """One structured extraction of the executive team + board."""
    members: List[RosterMember] = Field(default_factory=list)
    as_of: str = Field(
        default="",
        description="Filing date the roster reflects, e.g. '2026 DEF 14A' "
                    "or '10-K FY2025' — so the panel can say 'as of'.")
    source_form: str = Field(
        default="",
        description="Primary source: 'DEF 14A', '10-K Part I', or 'FMP'.")
    evidence_quotes: List[BusinessEvidence] = Field(
        default_factory=list,
        description="Verbatim filing quotes grounding each member's bio / "
                    "prior_roles, tagged 'roster:{name}:bio' or "
                    "'roster:{name}:prior_roles'. REQUIRED per member; omit "
                    "any bio claim you cannot support with a quote.")


# ── Evidence coverage (read/verify path) ────────────────────────────────────

def is_grounded_extraction(data: Optional[Dict[str, Any]]) -> bool:
    """True when produced with the evidence-quote requirement."""
    return bool((data or {}).get("evidence_schema_version"))


def _evidence_keys(data: Optional[Dict[str, Any]]) -> set:
    keys = set()
    for q in (data or {}).get("evidence_quotes") or []:
        c = (q.get("claim") or "").strip().lower()
        if c:
            keys.add(c)
    return keys


def member_bio_is_sourced(evidence_keys: set, name: str) -> bool:
    """Whether a member's bio/history is backed by any evidence quote."""
    n = (name or "").strip().lower()
    if not n:
        return False
    prefix = f"roster:{n}"
    return any(k == prefix or k.startswith(prefix + ":") for k in evidence_keys)


def verify_roster_evidence(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map each member with a non-empty bio to sourced/unsourced.

    Legacy (non-grounded) extractions flag nothing — no evidence layer to
    check against. Members whose bio is unsourced are surfaced so the
    panel can flag them and the caller can suppress unsupported prose.
    """
    if not is_grounded_extraction(data):
        return {"grounded": False, "sourced": [], "unsourced": []}
    keys = _evidence_keys(data)
    sourced, unsourced = [], []
    for m in (data or {}).get("members") or []:
        name = (m or {}).get("name") or ""
        bio = str((m or {}).get("bio_summary") or "").strip()
        prior = (m or {}).get("prior_roles") or []
        if not name or (not bio and not prior):
            continue  # nothing asserted about this person's history
        (sourced if member_bio_is_sourced(keys, name) else unsourced).append(name)
    return {"grounded": True, "sourced": sourced, "unsourced": unsourced}


# ── FMP name cross-check (hallucination guard) ──────────────────────────────

_NAME_STRIP = re.compile(r"[.,]|\b(jr|sr|ii|iii|iv|dr|mr|ms|mrs|phd|mba|cfa)\b",
                         re.IGNORECASE)


def _name_tokens(name: str) -> List[str]:
    """Lowercased alphabetic tokens with titles/suffixes removed."""
    cleaned = _NAME_STRIP.sub(" ", (name or "").lower())
    return [t for t in re.split(r"\s+", cleaned) if t.isalpha()]


def _names_match(a: str, b: str) -> bool:
    """Fuzzy person-name match: same last token AND first-initial agree,
    or ≥2 shared tokens (handles middle names / 'Last, First')."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    shared = set(ta) & set(tb)
    if len(shared) >= 2:
        return True
    return ta[-1] == tb[-1] and ta[0][:1] == tb[0][:1]


def crosscheck_roster_against_fmp(
    data: Optional[Dict[str, Any]],
    fmp_executives: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Flag extracted member names that FMP's key-executives doesn't know.

    FMP covers executives (not the full board), so a director-only name
    being absent is expected — we only treat an EXECUTIVE-typed member
    with no FMP match as a hallucination signal. Returns
    ``{"checked": bool, "unverified_execs": [...]}``. ``checked`` is False
    when FMP data is unavailable (offline / quota / foreign filer).
    """
    if not fmp_executives:
        return {"checked": False, "unverified_execs": []}
    fmp_names = [(e or {}).get("name") or "" for e in fmp_executives]
    unverified = []
    for m in (data or {}).get("members") or []:
        mtype = str((m or {}).get("member_type") or "").lower()
        if "exec" not in mtype and "both" not in mtype:
            continue  # directors aren't in FMP's exec list — don't flag
        name = (m or {}).get("name") or ""
        if name and not any(_names_match(name, fn) for fn in fmp_names):
            unverified.append(name)
    return {"checked": True, "unverified_execs": unverified}


# ── Cache ───────────────────────────────────────────────────────────────────

def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


def cached_management_roster(ticker: str) -> Optional[Dict[str, Any]]:
    """Cache-only read (no LLM) — the UI/report read path."""
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
        if time.time() - blob.get("fetched_at", 0) > _TTL_SECONDS:
            return None
        return blob.get("data")
    except Exception:
        return None


def _write_cache(ticker: str, data: Dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(ticker).write_text(json.dumps(
            {"fetched_at": time.time(), "data": data}, indent=2))
    except Exception:
        pass


# ── Extraction ──────────────────────────────────────────────────────────────

_PROMPT = """You are an equity research analyst compiling the MANAGEMENT ROSTER \
for {company} (ticker {ticker}) — the executive team AND the board of directors \
— from the company's proxy statement (DEF 14A) and/or the 10-K's "Information \
about our Executive Officers" section.

For EACH executive officer and EACH director, capture: name; role (CEO/CFO/COO/\
Chair/Lead Independent Director/Director/etc.); member_type ('executive', \
'director', or 'both'); age and since_year/tenure_years if disclosed; a 2-3 \
sentence bio_summary of their career history; prior_roles (prior positions/\
companies); committees (for directors); other_public_boards.

EVIDENCE — REQUIRED. Every bio_summary and each material prior_roles claim MUST \
be supported by a VERBATIM quote from the filing (≤300 chars, no paraphrase) in \
evidence_quotes, tagged via `claim` as 'roster:{{name}}:bio' or \
'roster:{{name}}:prior_roles' (use the person's name lowercased). If you CANNOT \
support a person's history with a verbatim quote, leave bio_summary/prior_roles \
BLANK for them — do NOT fabricate or infer a career history. Name, role, and \
committee memberships may come from the filing's roster/table without a prose \
quote; the narrative HISTORY is what needs grounding.

Set as_of to the filing (e.g. '2026 DEF 14A') and source_form to the primary \
source. Use the filing's own language. Return the structured object."""


def extract_management_roster(
    ticker: str, company: str = "",
    *, def14a_text: str = "", tenk_part1_text: str = "",
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Run the structured roster extraction and cache it.

    Reads cache unless ``force=True``. Sources are the DEF 14A text and/or
    the 10-K Part I executive-officers block (either may be empty). Returns
    the extracted dict (with names cross-checked against FMP) or None on
    any failure / no source text. Called once from the Stage-4 extraction
    node with the filing text already in hand — the run is user-authorized.
    """
    if not force:
        cached = cached_management_roster(ticker)
        if cached is not None:
            return cached
    parts = []
    if (def14a_text or "").strip():
        parts.append("=== DEF 14A PROXY ===\n" + def14a_text.strip()[:120000])
    if (tenk_part1_text or "").strip():
        parts.append("=== 10-K PART I (EXECUTIVE OFFICERS) ===\n"
                     + tenk_part1_text.strip()[:20000])
    if not parts:
        return None
    source_text = "\n\n".join(parts)
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.prompts import ChatPromptTemplate
        from config import MODEL_NAME
    except Exception:
        return None
    try:
        llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.1)
        structured = llm.with_structured_output(ManagementRoster)
        prompt = ChatPromptTemplate.from_template(
            _PROMPT + "\n\n=== FILING TEXT ===\n{text}")
        result: ManagementRoster = (prompt | structured).invoke({
            "company": company or ticker, "ticker": ticker, "text": source_text,
        })
        data = result.model_dump()
        data["evidence_schema_version"] = _EVIDENCE_SCHEMA_VERSION
        # Name cross-check against FMP (best-effort; never blocks).
        try:
            from aletheia.data import fmp_client
            execs = fmp_client.fetch_key_executives(ticker)
            data["fmp_crosscheck"] = crosscheck_roster_against_fmp(data, execs)
        except Exception:
            data["fmp_crosscheck"] = {"checked": False, "unverified_execs": []}
        _write_cache(ticker, data)
        return data
    except Exception as exc:
        print(f"  ⚠ management_roster extraction failed for {ticker}: "
              f"{type(exc).__name__}: {exc}")
        return None


__all__ = [
    "extract_management_roster", "cached_management_roster",
    "ManagementRoster", "RosterMember",
    "verify_roster_evidence", "member_bio_is_sourced", "is_grounded_extraction",
    "crosscheck_roster_against_fmp",
]
