"""
aletheia/data/database.py

DuckDB Versioned Database
==========================
Provides a structured, versioned, queryable store for:
  - Cleaned company records (from cleaning_engine.py)
  - Quantitative screen results (from quantitative_screens.py)
  - Cleaning flags and audit trail

Why DuckDB:
  - Reads existing Parquet files natively (zero migration cost)
  - In-process (no server to manage)
  - Full SQL analytical queries
  - Excellent pandas integration

Schema design:
  - company_records    : one row per (ticker, fiscal_year, version)
  - cleaning_flags     : one row per flag raised by any domain
  - screen_results     : Beneish, Sloan, EPV per (ticker, fiscal_year)
  - universe_status    : latest status per ticker (for monitoring dashboard)

Versioning:
  Each cleaning run for the same (ticker, fiscal_year) gets an
  incrementing version number. The latest version is always queryable
  via the `company_records_latest` view.

Usage:
    from aletheia.data.database import InvestmentDatabase

    db = InvestmentDatabase()
    db.upsert_record(cleaned_record)
    db.upsert_screens(screen_result)

    # Query latest records
    df = db.query("SELECT * FROM company_records_latest WHERE ticker = 'AAPL'")
    print(df)
"""

import datetime
import json
import threading
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from dataclasses import dataclass

import pandas as pd

try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    print("⚠ DuckDB not installed. Run: pip install duckdb")
    print("  Database features will be unavailable until installed.")

from aletheia.data.cleaning_engine import CleanedRecord, CleaningFlag, CleaningEngine
from aletheia.data.quantitative_screens import ScreenResult, QuantitativeScreens
from aletheia.data.exceptions import IngestionError, MissingFieldError, ValidationError, SourceFetchError
from aletheia.data.ingestion_validator import IngestionValidator


@dataclass
class CalculationOutput:
    ticker: str
    fiscal_year: int
    status: Literal["success", "missing_fields", "validation_failed", "error"]
    cleaned_record: Optional[CleanedRecord] = None
    screen_result: Optional[ScreenResult] = None
    error_detail: Optional[str] = None



# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

class InvestmentDatabase:
    """
    Manages the DuckDB investment database.

    The database file lives at: valuation_data/database/investment.duckdb
    This is persistent across sessions.

    Parquet files in valuation_data/canonical/ are also directly queryable:
        db.query("SELECT * FROM read_parquet('valuation_data/canonical/financials/AAPL.parquet')")
    """

    DEFAULT_DB_PATH = "valuation_data/database/investment.duckdb"

    # Per-process record of which DB files have already had their schema
    # initialized. Schema init runs `CREATE OR REPLACE VIEW`, which holds
    # a catalog write lock. When multiple threads open the same DB
    # concurrently (e.g. parallelized /universe loop) they all try to
    # recreate the view and collide with TransactionException. Skipping
    # init after the first connection per file prevents the collision
    # without compromising correctness — the schema only changes when
    # this code does, which means only at process restart.
    #
    # The lock guards the set-check + set-add sequence; without it two
    # threads can pass the membership test before either records the
    # init, then both run init in parallel and trip the same write-write
    # conflict the set was supposed to prevent.
    _schema_initialized_paths: set = set()
    _schema_init_lock = threading.Lock()

    def __init__(self, db_path: str = None, verbose: bool = True):
        if not DUCKDB_AVAILABLE:
            raise RuntimeError(
                "DuckDB is required. Install with: pip install duckdb"
            )

        self.db_path = Path(db_path or self.DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose

        self._conn = duckdb.connect(str(self.db_path))

        path_key = str(self.db_path.resolve())
        with InvestmentDatabase._schema_init_lock:
            if path_key not in InvestmentDatabase._schema_initialized_paths:
                self._initialize_schema()
                InvestmentDatabase._schema_initialized_paths.add(path_key)

        if verbose:
            print(f"✓ Database connected: {self.db_path}")

    def close(self):
        if self._conn:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Schema initialization
    # ─────────────────────────────────────────────────────────────────────────

    def _initialize_schema(self):
        """Create all tables and views if they do not exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS company_records (
                -- Primary key
                id                      VARCHAR PRIMARY KEY,
                ticker                  VARCHAR NOT NULL,
                fiscal_year             INTEGER NOT NULL,
                version                 INTEGER NOT NULL DEFAULT 1,
                period_end_date         DATE,
                cleaned_at              VARCHAR NOT NULL,

                -- Quality
                overall_quality_score   DOUBLE,
                warning_count           INTEGER DEFAULT 0,
                error_count             INTEGER DEFAULT 0,

                -- Core cleaned metrics
                clean_Revenue           DOUBLE,
                clean_NormalizedEBIT    DOUBLE,
                clean_NOPAT             DOUBLE,
                clean_EBITDA            DOUBLE,
                clean_EBITDAR           DOUBLE,
                clean_EBITDA_ExcludingSBC DOUBLE,
                clean_SGA_Combined      DOUBLE,
                clean_GeneralAndAdministrative DOUBLE,
                clean_SellingAndMarketing DOUBLE,
                clean_FCF               DOUBLE,
                clean_FCFF              DOUBLE,
                clean_CashTaxRate       DOUBLE,
                clean_GAAP_TaxRate      DOUBLE,

                -- Derived metrics
                derived_EBITDA          DOUBLE,
                derived_OperatingIncome DOUBLE,
                derived_Depreciation_Total DOUBLE,
                derived_CapEx           DOUBLE,
                derived_FCF             DOUBLE,
                derived_FCFF            DOUBLE,
                derived_ROIC            DOUBLE,
                derived_ROE             DOUBLE,
                derived_NetDebt         DOUBLE,
                derived_InvestedCapital DOUBLE,
                derived_GrossMargin_Pct DOUBLE,
                derived_EBIT_Margin_Pct DOUBLE,
                derived_EBITDA_Margin_Pct DOUBLE,
                derived_FCF_Margin_Pct  DOUBLE,

                -- Equity bridge items
                clean_PensionDeficit_ForEquityBridge DOUBLE,
                clean_LeaseDebt_ForEquityBridge      DOUBLE,
                clean_JVA_Income_Isolated            DOUBLE,

                -- Domain-level scores
                domain_score_D1_NonRecurring    DOUBLE,
                domain_score_D2_JVA             DOUBLE,
                domain_score_D3_EBITNorm        DOUBLE,
                domain_score_D4_AccountingPolicy DOUBLE,
                domain_score_D5_Lease           DOUBLE,
                domain_score_D6_Pension         DOUBLE,
                domain_score_D7_SBC             DOUBLE,
                domain_score_D8_Revenue         DOUBLE,
                domain_score_D9_WorkingCapital  DOUBLE,
                domain_score_D10_Tax            DOUBLE,

                -- SBC and dilution
                clean_SBC               DOUBLE,
                clean_SBC_PctFCF        DOUBLE,
                clean_ShareDilution_Pct DOUBLE,
                clean_NetBuyback_AfterSBC DOUBLE,

                -- Raw key metrics (pre-cleaning)
                raw_Revenue             DOUBLE,
                raw_NetIncome           DOUBLE,
                raw_TotalAssets         DOUBLE,
                raw_TotalEquity         DOUBLE,
                raw_LongTermDebt        DOUBLE,
                raw_Cash                DOUBLE,
                raw_Depreciation_Total_Aggregate DOUBLE,
                raw_Depreciation_Tangible DOUBLE,
                raw_IntangibleAmortization DOUBLE,
                raw_FinanceLeaseAmortization DOUBLE,
                raw_CapitalizedSoftwareAmortization DOUBLE,
                raw_CapEx               DOUBLE,
                raw_RnD                 DOUBLE,
                raw_COGS                DOUBLE,
                raw_OperatingIncome     DOUBLE,
                raw_TotalLiabilities    DOUBLE,
                raw_LiabilitiesCurrent  DOUBLE,
                raw_SharesDiluted       DOUBLE,
                raw_SharesOutstanding   DOUBLE,
                clean_SharesDiluted     DOUBLE,
                period_end_date_missing BOOLEAN DEFAULT FALSE,
                raw_LongTermInvestments DOUBLE,
                raw_CurrentPortionLongTermDebt DOUBLE,
                raw_LiabilitiesNoncurrent DOUBLE,

                -- Serialized full record (for deep inspection)
                raw_json                VARCHAR,
                clean_json              VARCHAR,
                warnings_json           VARCHAR,
                errors_json             VARCHAR
            )
        """)

        # Schema migrations: add columns introduced after the original
        # CREATE TABLE without forcing a rebuild of existing DBs.
        for col in (
            "clean_EBITDA_ExcludingSBC",
            "clean_SGA_Combined",
            "clean_GeneralAndAdministrative",
            "clean_SellingAndMarketing",
        ):
            self._conn.execute(
                f"ALTER TABLE company_records ADD COLUMN IF NOT EXISTS {col} DOUBLE"
            )

        # FMP Gate A validation receipt — stamps every cleaned record with
        # the result of the FMP cross-check at ingestion time. Read by
        # Gate D (lead.save_report) when assembling the serving JSON's
        # _validation block.
        for col, sqltype in (
            ("fmp_validation_status",      "VARCHAR"),   # validated|skipped|drift
            ("fmp_validation_skip_reason", "VARCHAR"),   # NULL when status != skipped
            ("fmp_validation_json",        "VARCHAR"),   # full payload as JSON
            ("fmp_validated_at",           "VARCHAR"),   # ISO8601
        ):
            self._conn.execute(
                f"ALTER TABLE company_records ADD COLUMN IF NOT EXISTS {col} {sqltype}"
            )

        # Phase Q-1: period dimension. 'FY' for annual records (today's
        # only path); 'Q1'..'Q4' for quarterly; 'TTM' for trailing-twelve-
        # month derivations. Backfilled to 'FY' on existing rows by the
        # column default. Joined into the (ticker, fiscal_year, period)
        # uniqueness key used by company_records_latest.
        self._conn.execute(
            "ALTER TABLE company_records "
            "ADD COLUMN IF NOT EXISTS period VARCHAR DEFAULT 'FY'"
        )
        self._conn.execute(
            "UPDATE company_records SET period='FY' WHERE period IS NULL"
        )

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cleaning_flags (
                id              VARCHAR PRIMARY KEY,
                ticker          VARCHAR NOT NULL,
                fiscal_year     INTEGER NOT NULL,
                version         INTEGER NOT NULL,
                cleaned_at      VARCHAR NOT NULL,
                domain          INTEGER,
                domain_name     VARCHAR,
                metric          VARCHAR,
                raw_value       DOUBLE,
                adjusted_value  DOUBLE,
                action          VARCHAR,
                reason          VARCHAR,
                confidence      DOUBLE
            )
        """)

        # Phase Q-1: period dimension on cleaning_flags. Same default-'FY'
        # backfill so legacy rows behave identically.
        self._conn.execute(
            "ALTER TABLE cleaning_flags "
            "ADD COLUMN IF NOT EXISTS period VARCHAR DEFAULT 'FY'"
        )
        self._conn.execute(
            "UPDATE cleaning_flags SET period='FY' WHERE period IS NULL"
        )

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS screen_results (
                id                      VARCHAR PRIMARY KEY,
                ticker                  VARCHAR NOT NULL,
                fiscal_year             INTEGER NOT NULL,
                screened_at             VARCHAR NOT NULL,

                -- Beneish
                beneish_m_score         DOUBLE,
                beneish_flagged         BOOLEAN,
                beneish_completeness    DOUBLE,
                beneish_DSRI            DOUBLE,
                beneish_GMI             DOUBLE,
                beneish_AQI             DOUBLE,
                beneish_SGI             DOUBLE,
                beneish_DEPI            DOUBLE,
                beneish_SGAI            DOUBLE,
                beneish_LVGI            DOUBLE,
                beneish_TATA            DOUBLE,

                -- Sloan
                sloan_accrual_ratio     DOUBLE,
                sloan_signal            VARCHAR,
                sloan_flagged           BOOLEAN,

                -- EPV
                epv                     DOUBLE,
                epv_per_share           DOUBLE,
                epv_to_price_ratio      DOUBLE,
                epv_signal              VARCHAR,

                -- Summary
                any_flagged             BOOLEAN
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS universe_status (
                ticker              VARCHAR PRIMARY KEY,
                company_name        VARCHAR,
                sector              VARCHAR,
                industry            VARCHAR,
                last_cleaned_at     VARCHAR,
                last_fy_cleaned     INTEGER,
                latest_quality_score DOUBLE,
                beneish_flagged     BOOLEAN,
                sloan_flagged       BOOLEAN,
                warning_count       INTEGER,
                error_count         INTEGER,
                status              VARCHAR    -- "clean", "review", "blocked"
            )
        """)


        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS quarantine_records (
                -- Identity
                id                  VARCHAR PRIMARY KEY,
                ticker              VARCHAR NOT NULL,
                fiscal_year         INTEGER NOT NULL,
                quarantined_at      VARCHAR NOT NULL,

                -- Why quarantined
                has_errors          BOOLEAN NOT NULL DEFAULT TRUE,
                failure_count       INTEGER NOT NULL DEFAULT 0,
                failures_json       VARCHAR,   -- JSON array of ValidationFailure dicts

                -- Full record snapshot for debugging
                revenue             DOUBLE,
                ebitda              DOUBLE,
                da                  DOUBLE,
                capex               DOUBLE,
                roic                DOUBLE,
                raw_json            VARCHAR,
                clean_json          VARCHAR
            )
        """)

        # ── Agent runs ─────────────────────────────────────────────────────────
        # Versioned store for LLM-authored agent outputs. Lives alongside
        # `company_records` (which versions cleaned data) so a complete
        # historical reconstruction is possible: pin a (ticker, code git_sha,
        # cleaned-data version) and you can replay exactly what the agents
        # saw and produced at that point in time.
        #
        # Deliberate non-features:
        #   - No deterministic columns (revenue, ebitda, wacc, scenarios, etc).
        #     Those are recomputed from `company_records_latest`. Storing them
        #     here would re-introduce the JSON-as-truth duplication that
        #     caused the ACN-class bug. The `upsert_agent_run` writer enforces
        #     this with a whitelist.
        #   - No "current" flag. The latest version per ticker is derived via
        #     the `agent_runs_latest` view, not by mutating older rows.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id                  VARCHAR PRIMARY KEY,
                ticker              VARCHAR NOT NULL,
                version             INTEGER NOT NULL,
                generated_at        VARCHAR NOT NULL,
                fiscal_year         INTEGER NOT NULL,
                git_sha             VARCHAR,
                economic_reality    VARCHAR,    -- JSON
                contrarian_analysis VARCHAR,    -- JSON
                investment_thesis   VARCHAR,    -- JSON
                agent_scenarios     VARCHAR,    -- JSON
                UNIQUE (ticker, version)
            )
        """)

        # ── Qualitative assessments ────────────────────────────────────────────
        # Versioned 1-7 scores across 18-19 analytical dimensions. Same
        # design pattern as agent_runs: each submission appends a new row,
        # `qualitative_assessments_latest` view exposes the most recent
        # per (ticker, dimension_id).
        #
        # Three guardrails enforced by the writer (`upsert_qualitative_assessment`):
        #   1. dimension_id must match a known catalog entry
        #   2. source_category must match the catalog entry's declared category
        #   3. score must be in [1.0, 7.0] when present (PENDING_DATA may be NULL)
        #
        # No score-bucketing logic stored here — bucket cutoffs live in the
        # catalog (code) so they're reviewable + versioned via code_version.
        # When a deterministic dimension's formula or buckets change, the
        # next recompute writes a new row with the new code_git_sha; prior
        # rows remain interpretable as historical snapshots.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS qualitative_assessments (
                assessment_id     VARCHAR PRIMARY KEY,
                ticker            VARCHAR NOT NULL,
                dimension_id      VARCHAR NOT NULL,
                score             DOUBLE,           -- NULL for PENDING_DATA
                sub_scores        VARCHAR,          -- JSON, HITL only
                narrative         VARCHAR,          -- optional <500 chars
                source_category   VARCHAR NOT NULL,
                source_payload    VARCHAR,          -- JSON
                assessed_at       VARCHAR NOT NULL,
                analyst_id        VARCHAR NOT NULL,
                code_git_sha      VARCHAR,
                input_fingerprint VARCHAR,          -- DETERMINISTIC only
                UNIQUE (ticker, dimension_id, assessed_at)
            )
        """)

        # ── Views ─────────────────────────────────────────────────────────────

        # Latest version of each (company, fiscal year, period). The period
        # join key keeps FY rows separate from quarterly + TTM derivations
        # so a single ticker can expose multiple period rows simultaneously.
        # screen_results has no period column yet (per Phase Q-1 scope),
        # so the screen join only attaches to FY rows.
        self._conn.execute("""
            CREATE OR REPLACE VIEW company_records_latest AS
            WITH latest_screens AS (
                SELECT ticker, fiscal_year,
                    beneish_m_score, beneish_flagged,
                    sloan_accrual_ratio, sloan_flagged,
                    epv, epv_per_share, epv_to_price_ratio, epv_signal,
                    any_flagged,
                    ROW_NUMBER() OVER (
                        PARTITION BY ticker, fiscal_year
                        ORDER BY screened_at DESC
                    ) AS rn
                FROM screen_results
            )
            SELECT cr.*,
                   ls.beneish_m_score,
                   ls.beneish_flagged,
                   ls.sloan_accrual_ratio,
                   ls.sloan_flagged,
                   ls.epv,
                   ls.epv_per_share,
                   ls.epv_to_price_ratio,
                   ls.epv_signal,
                   ls.any_flagged
            FROM company_records cr
            INNER JOIN (
                SELECT ticker, fiscal_year, period, MAX(version) AS max_version
                FROM company_records
                GROUP BY ticker, fiscal_year, period
            ) latest
            ON cr.ticker = latest.ticker
            AND cr.fiscal_year = latest.fiscal_year
            AND cr.period = latest.period
            AND cr.version = latest.max_version
            LEFT JOIN latest_screens ls
            ON cr.ticker = ls.ticker
            AND cr.fiscal_year = ls.fiscal_year
            AND cr.period = 'FY'
            AND ls.rn = 1
        """)

        # Flagged companies requiring review
        # Uses latest screen_results per (ticker, fiscal_year) to avoid duplicates
        self._conn.execute("""
            CREATE OR REPLACE VIEW flagged_for_review AS
            WITH latest_screens AS (
                SELECT ticker, fiscal_year,
                    beneish_m_score, beneish_flagged,
                    sloan_accrual_ratio, sloan_flagged,
                    any_flagged,
                    ROW_NUMBER() OVER (
                        PARTITION BY ticker, fiscal_year
                        ORDER BY screened_at DESC
                    ) AS rn
                FROM screen_results
            )
            SELECT
                cr.ticker,
                cr.fiscal_year,
                cr.overall_quality_score,
                cr.warning_count,
                ls.beneish_m_score,
                ls.beneish_flagged,
                ls.sloan_accrual_ratio,
                ls.sloan_flagged,
                ls.any_flagged,
                cr.cleaned_at
            FROM company_records_latest cr
            LEFT JOIN latest_screens ls
                ON cr.ticker = ls.ticker
                AND cr.fiscal_year = ls.fiscal_year
                AND ls.rn = 1
            WHERE cr.period = 'FY'
              AND (ls.any_flagged = TRUE
                   OR cr.warning_count > 3
                   OR cr.overall_quality_score < 0.70)
            ORDER BY ls.beneish_m_score DESC NULLS LAST
        """)

        # Portfolio-level quality summary
        self._conn.execute("""
            CREATE OR REPLACE VIEW portfolio_quality_summary AS
            SELECT
                cr.ticker,
                MAX(cr.fiscal_year) AS latest_fy,
                AVG(cr.overall_quality_score) AS avg_quality,
                SUM(cr.warning_count) AS total_warnings,
                MAX(CASE WHEN sr.beneish_flagged THEN 1 ELSE 0 END) AS ever_beneish_flagged,
                AVG(sr.sloan_accrual_ratio) AS avg_sloan_ratio,
                AVG(cr.derived_ROIC) AS avg_roic,
                AVG(cr.derived_FCF_Margin_Pct) AS avg_fcf_margin
            FROM company_records_latest cr
            LEFT JOIN screen_results sr
                ON cr.ticker = sr.ticker AND cr.fiscal_year = sr.fiscal_year
            WHERE cr.period = 'FY'
            GROUP BY cr.ticker
            ORDER BY avg_quality DESC
        """)

        # Latest agent run per ticker — single row per ticker, the most
        # recently written version. Older versions are preserved in
        # `agent_runs` for audit but read paths only need the latest.
        self._conn.execute("""
            CREATE OR REPLACE VIEW agent_runs_latest AS
            SELECT ar.*
            FROM agent_runs ar
            INNER JOIN (
                SELECT ticker, MAX(version) AS max_version
                FROM agent_runs
                GROUP BY ticker
            ) latest
            ON ar.ticker = latest.ticker
            AND ar.version = latest.max_version
        """)

        # Latest qualitative assessment per (ticker, dimension_id). One
        # row per dimension per ticker exposes the most recent score for
        # the read API; history is preserved in `qualitative_assessments`
        # for audit and future "did the analyst change their mind?" UIs.
        self._conn.execute("""
            CREATE OR REPLACE VIEW qualitative_assessments_latest AS
            SELECT qa.*
            FROM qualitative_assessments qa
            INNER JOIN (
                SELECT ticker, dimension_id, MAX(assessed_at) AS latest_at
                FROM qualitative_assessments
                GROUP BY ticker, dimension_id
            ) latest
            ON qa.ticker = latest.ticker
            AND qa.dimension_id = latest.dimension_id
            AND qa.assessed_at = latest.latest_at
        """)

        if self.verbose:
            print("  ✓ Schema initialized (company_records, cleaning_flags, screen_results, universe_status, agent_runs, qualitative_assessments)")

    # ─────────────────────────────────────────────────────────────────────────
    # Write operations
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_record(self, record: CleanedRecord) -> int:
        """
        Insert a CleanedRecord into the database.
        Auto-increments version if a record for (ticker, fiscal_year) already exists.
        Returns the version number written.
        """
        # Schema-contract assertion (Phase 2 framework). Single persistence
        # boundary for every record written to DuckDB regardless of source
        # path (FY cleaning, SEC TTM, FMP TTM, add-ticker pipeline). The
        # function honors ALETHEIA_GUARD_MODE: 'off' is a no-op, 'shadow'
        # logs violations, 'soft' logs + surfaces, 'hard' raises and the
        # caller's try/except decides whether to skip persist.
        from aletheia.calculations import validate_cleaned_record_schema_contract
        try:
            validate_cleaned_record_schema_contract(record)
        except Exception:
            # In hard mode this propagates and the caller decides; the
            # framework's responsibility is just to raise loudly. We
            # intentionally do NOT swallow here.
            raise

        # Get next version number per (ticker, fiscal_year, period). Period
        # defaults to 'FY' on legacy CleanedRecord instances; quarterly +
        # TTM records carry their own period and version independently of
        # the FY row.
        t = str(record.ticker)
        fy = int(record.fiscal_year)
        period = getattr(record, "period", "FY") or "FY"
        existing = self._conn.execute(
            "SELECT MAX(version) FROM company_records "
            "WHERE ticker=? AND fiscal_year=? AND period=?",
            [t, fy, period]
        ).fetchone()[0]
        version = (existing or 0) + 1
        record.version = version

        # Primary key includes period so quarterly / TTM rows don't
        # collide with the FY row for the same (ticker, fy).
        record_id = (
            f"{record.ticker}_{record.fiscal_year}_v{version}"
            if period == "FY"
            else f"{record.ticker}_{record.fiscal_year}_{period}_v{version}"
        )

        # Build the row — explicit columns for structured fields
        row = {
            "id": record_id,
            "ticker": record.ticker,
            "fiscal_year": record.fiscal_year,
            "period": period,
            "version": version,
            "period_end_date": record.period_end_date,
            "cleaned_at": record.cleaned_at,
            "overall_quality_score": record.overall_quality_score,
            "warning_count": len(record.cleaning_warnings),
            "error_count": len(record.blocking_errors),

            # Cleaned metrics
            "clean_Revenue": record.clean.get("Revenue"),
            "clean_NormalizedEBIT": record.clean.get("NormalizedEBIT"),
            "clean_NOPAT": record.clean.get("NOPAT"),
            "clean_EBITDA": record.clean.get("EBITDA"),
            "clean_EBITDAR": record.clean.get("EBITDAR"),
            "clean_EBITDA_ExcludingSBC": record.clean.get("EBITDA_ExcludingSBC"),
            "clean_SGA_Combined": record.clean.get("SGA_Combined"),
            "clean_GeneralAndAdministrative": record.clean.get("GeneralAndAdministrative"),
            "clean_SellingAndMarketing": record.clean.get("SellingAndMarketing"),
            "clean_FCF": record.clean.get("FCF"),
            "clean_FCFF": record.clean.get("FCFF"),
            "clean_CashTaxRate": record.clean.get("CashTaxRate"),
            "clean_GAAP_TaxRate": record.clean.get("GAAP_TaxRate"),

            # Derived
            "derived_EBITDA": record.derived.get("EBITDA"),
            "derived_OperatingIncome": record.derived.get("OperatingIncome"),
            "derived_Depreciation_Total": record.derived.get("Depreciation_Total"),
            "derived_CapEx": record.derived.get("CapEx"),
            "derived_FCF": record.derived.get("FCF"),
            "derived_FCFF": record.derived.get("FCFF"),
            "derived_ROIC": record.derived.get("ROIC"),
            "derived_ROE": record.derived.get("ROE"),
            "derived_NetDebt": record.derived.get("NetDebt"),
            "derived_InvestedCapital": record.derived.get("InvestedCapital"),
            "derived_GrossMargin_Pct": record.derived.get("GrossMargin_Pct"),
            "derived_EBIT_Margin_Pct": record.derived.get("EBIT_Margin_Pct"),
            "derived_EBITDA_Margin_Pct": record.derived.get("EBITDA_Margin_Pct"),
            "derived_FCF_Margin_Pct": record.derived.get("FCF_Margin_Pct"),

            # Equity bridge
            "clean_PensionDeficit_ForEquityBridge": record.clean.get("PensionDeficit_ForEquityBridge"),
            "clean_LeaseDebt_ForEquityBridge": record.clean.get("LeaseDebt_ForEquityBridge"),
            "clean_JVA_Income_Isolated": record.clean.get("JVA_Income_Isolated"),

            # Domain scores
            "domain_score_D1_NonRecurring": record.domain_scores.get("D1_NonRecurring"),
            "domain_score_D2_JVA": record.domain_scores.get("D2_JVA"),
            "domain_score_D3_EBITNorm": record.domain_scores.get("D3_EBITNorm"),
            "domain_score_D4_AccountingPolicy": record.domain_scores.get("D4_AccountingPolicy"),
            "domain_score_D5_Lease": record.domain_scores.get("D5_Lease"),
            "domain_score_D6_Pension": record.domain_scores.get("D6_Pension"),
            "domain_score_D7_SBC": record.domain_scores.get("D7_SBC"),
            "domain_score_D8_Revenue": record.domain_scores.get("D8_Revenue"),
            "domain_score_D9_WorkingCapital": record.domain_scores.get("D9_WorkingCapital"),
            "domain_score_D10_Tax": record.domain_scores.get("D10_Tax"),

            # SBC
            "clean_SBC": record.clean.get("SBC"),
            "clean_SBC_PctFCF": record.clean.get("SBC_PctFCF"),
            "clean_ShareDilution_Pct": record.clean.get("ShareDilution_Pct"),
            "clean_NetBuyback_AfterSBC": record.clean.get("NetBuyback_AfterSBC"),

            # Raw key metrics
            "raw_Revenue": record.raw.get("Revenue"),
            "raw_NetIncome": record.raw.get("NetIncome"),
            "raw_TotalAssets": record.raw.get("TotalAssets"),
            "raw_TotalEquity": record.raw.get("TotalEquity"),
            "raw_LongTermDebt": record.raw.get("LongTermDebt"),
            "raw_Cash": record.raw.get("Cash"),
            "raw_Depreciation_Total_Aggregate": record.raw.get("Depreciation_Total_Aggregate"),
            "raw_Depreciation_Tangible": record.raw.get("Depreciation_Tangible"),
            "raw_IntangibleAmortization": record.raw.get("IntangibleAmortization"),
            "raw_FinanceLeaseAmortization": record.raw.get("FinanceLeaseAmortization"),
            "raw_CapitalizedSoftwareAmortization": record.raw.get("CapitalizedSoftwareAmortization"),
            "raw_CapEx": record.raw.get("CapEx"),
            "raw_RnD": record.raw.get("R&D"),
            "raw_COGS": record.raw.get("COGS"),
            "raw_OperatingIncome": record.raw.get("OperatingIncome"),
            "raw_TotalLiabilities": record.raw.get("TotalLiabilities"),
            "raw_LiabilitiesCurrent": record.raw.get("LiabilitiesCurrent"),
            "raw_SharesDiluted": record.raw.get("SharesDiluted"),
            "raw_SharesOutstanding": record.raw.get("SharesOutstanding"),
            "clean_SharesDiluted": record.clean.get("SharesDiluted"),
            "period_end_date_missing": record.period_end_date_missing,
            "raw_LongTermInvestments": record.raw.get("LongTermInvestments"),
            "raw_CurrentPortionLongTermDebt": record.raw.get("CurrentPortionLongTermDebt"),
            "raw_LiabilitiesNoncurrent": record.raw.get("LiabilitiesNoncurrent"),

            # Full serialized record
            "raw_json": json.dumps({k: v for k, v in record.raw.items() if v is not None}),
            "clean_json": json.dumps({k: v for k, v in record.clean.items() if v is not None}),
            "warnings_json": json.dumps(record.cleaning_warnings),
            "errors_json": json.dumps(record.blocking_errors),

            # Gate A FMP-validation receipt. Defaults to "skipped /
            # not_run" on legacy records (validation_engine wasn't there
            # at their original ingestion). New records ingested under
            # the validation gate land here as either `validated`,
            # `drift`, or `skipped` (with reason). `blocking_drift` rows
            # are not persisted — caller skips this whole write.
            "fmp_validation_status":      (record.fmp_validation or {}).get("status", "not_run"),
            "fmp_validation_skip_reason": (record.fmp_validation or {}).get("skip_reason"),
            "fmp_validation_json":        json.dumps(record.fmp_validation or {}, default=str),
            "fmp_validated_at":           (record.fmp_validation or {}).get("fetched_at"),
        }

        df = pd.DataFrame([row])
        self._conn.execute("INSERT INTO company_records BY NAME SELECT * FROM df")

        # Write cleaning flags
        self._upsert_flags(record, version)

        # Update universe status
        self._update_universe_status(record)

        if self.verbose:
            print(f"  ✓ DB: {record.ticker} FY{record.fiscal_year} v{version} written "
                  f"(quality={record.overall_quality_score:.2f})")

        return version

    def _upsert_flags(self, record: CleanedRecord, version: int):
        """Write all cleaning flags for this record."""
        if not record.flags:
            return

        period = getattr(record, "period", "FY") or "FY"
        period_suffix = "" if period == "FY" else f"_{period}"
        flag_rows = []
        for i, flag in enumerate(record.flags):
            flag_rows.append({
                "id": f"{record.ticker}_{record.fiscal_year}{period_suffix}_v{version}_f{i}",
                "ticker": record.ticker,
                "fiscal_year": record.fiscal_year,
                "period": period,
                "version": version,
                "cleaned_at": record.cleaned_at,
                "domain": flag.domain,
                "domain_name": flag.domain_name,
                "metric": flag.metric,
                "raw_value": flag.raw_value,
                "adjusted_value": flag.adjusted_value,
                "action": flag.action,
                "reason": flag.reason,
                "confidence": flag.confidence,
            })

        if flag_rows:
            df = pd.DataFrame(flag_rows)
            self._conn.execute("INSERT INTO cleaning_flags BY NAME SELECT * FROM df")

    def upsert_screens(self, result: ScreenResult):
        """Write screen results to the database."""
        screen_id = f"{result.ticker}_{result.fiscal_year}_{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"

        row = {
            "id": screen_id,
            "ticker": result.ticker,
            "fiscal_year": result.fiscal_year,
            "screened_at": datetime.datetime.utcnow().isoformat(),

            "beneish_m_score": result.beneish.m_score,
            "beneish_flagged": result.beneish.is_flagged,
            "beneish_completeness": result.beneish.data_completeness,
            "beneish_DSRI": result.beneish.components.get("DSRI"),
            "beneish_GMI": result.beneish.components.get("GMI"),
            "beneish_AQI": result.beneish.components.get("AQI"),
            "beneish_SGI": result.beneish.components.get("SGI"),
            "beneish_DEPI": result.beneish.components.get("DEPI"),
            "beneish_SGAI": result.beneish.components.get("SGAI"),
            "beneish_LVGI": result.beneish.components.get("LVGI"),
            "beneish_TATA": result.beneish.components.get("TATA"),

            "sloan_accrual_ratio": result.sloan.accrual_ratio,
            "sloan_signal": result.sloan.signal,
            "sloan_flagged": result.sloan.is_flagged,

            "epv": result.epv.epv,
            "epv_per_share": result.epv.epv_per_share,
            "epv_to_price_ratio": result.epv.epv_to_price_ratio,
            "epv_signal": result.epv.signal,

            "any_flagged": result.any_flagged,
        }

        df = pd.DataFrame([row])
        self._conn.execute("INSERT INTO screen_results SELECT * FROM df")

        if self.verbose:
            flagged_str = "⚠ FLAGGED" if result.any_flagged else "✓ OK"
            print(f"  ✓ DB screens: {result.ticker} FY{result.fiscal_year} {flagged_str}")

    def _update_universe_status(self, record: CleanedRecord):
        """Update the universe_status table for this ticker."""
        beneish_flag = False
        sloan_flag = False

        existing = self._conn.execute(
            "SELECT * FROM universe_status WHERE ticker = ?",
            [record.ticker]
        ).fetchdf()

        status = "clean"
        if record.blocking_errors:
            status = "blocked"
        elif record.cleaning_warnings or record.overall_quality_score < 0.80:
            status = "review"

        if existing.empty:
            self._conn.execute("""
                INSERT INTO universe_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                record.ticker, None, None, None,
                record.cleaned_at, record.fiscal_year,
                record.overall_quality_score,
                beneish_flag, sloan_flag,
                len(record.cleaning_warnings), len(record.blocking_errors),
                status
            ])
        else:
            self._conn.execute("""
                UPDATE universe_status SET
                    last_cleaned_at = ?,
                    last_fy_cleaned = ?,
                    latest_quality_score = ?,
                    warning_count = ?,
                    error_count = ?,
                    status = ?
                WHERE ticker = ?
            """, [
                record.cleaned_at, record.fiscal_year,
                record.overall_quality_score,
                len(record.cleaning_warnings), len(record.blocking_errors),
                status, record.ticker
            ])

    # ─────────────────────────────────────────────────────────────────────────
    # Agent runs — versioned LLM-authored payloads
    # ─────────────────────────────────────────────────────────────────────────

    # Whitelist of payload keys allowed in `agent_runs`. Any other key is
    # rejected — this is the structural guardrail that prevents future
    # callers from accidentally re-introducing JSON-as-truth duplication
    # (e.g. caching `clean_financials` or `phase2_valuation` alongside
    # the LLM payload). If a deterministic field belongs in the response,
    # it should be recomputed from `company_records_latest` at read time.
    _AGENT_RUN_PAYLOAD_KEYS = (
        "economic_reality",
        "contrarian_analysis",
        "investment_thesis",
        "agent_scenarios",
    )

    def upsert_agent_run(
        self,
        ticker: str,
        fiscal_year: int,
        llm_payload: Dict[str, Any],
        git_sha: Optional[str] = None,
    ) -> int:
        """Persist an agent-run output as a new versioned row.

        Args:
            ticker: Upper-cased ticker symbol.
            fiscal_year: The latest fiscal year the calc layer used as
                input for this run. Lets a future read pin the run to a
                specific cleaned-data state.
            llm_payload: Dict with exactly the four keys in
                `_AGENT_RUN_PAYLOAD_KEYS`. Extra keys raise ValueError.
                Missing keys are stored as JSON nulls — they're permitted
                because not every run produces every block (e.g. a run
                that bails before contrarian).
            git_sha: Optional code version at run time, for forensic
                "did this thesis pre-date the DCF cap fix?" queries.

        Returns:
            The version number written (1 for the first run per ticker,
            then monotonically increasing).
        """
        ticker = str(ticker).upper()

        unknown = set(llm_payload.keys()) - set(self._AGENT_RUN_PAYLOAD_KEYS)
        if unknown:
            raise ValueError(
                f"upsert_agent_run rejected deterministic-field keys "
                f"{sorted(unknown)}. agent_runs only stores LLM-authored "
                f"payloads ({list(self._AGENT_RUN_PAYLOAD_KEYS)}); "
                f"deterministic data must be recomputed from "
                f"company_records_latest."
            )

        existing = self._conn.execute(
            "SELECT MAX(version) FROM agent_runs WHERE ticker = ?",
            [ticker],
        ).fetchone()[0]
        version = (existing or 0) + 1
        run_id = f"{ticker}_v{version}"
        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def _encode(key: str) -> Optional[str]:
            v = llm_payload.get(key)
            if v is None:
                return None
            return json.dumps(v, default=str)

        self._conn.execute(
            """
            INSERT INTO agent_runs
            (id, ticker, version, generated_at, fiscal_year, git_sha,
             economic_reality, contrarian_analysis, investment_thesis, agent_scenarios)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id, ticker, version, generated_at, int(fiscal_year), git_sha,
                _encode("economic_reality"),
                _encode("contrarian_analysis"),
                _encode("investment_thesis"),
                _encode("agent_scenarios"),
            ],
        )

        if self.verbose:
            print(f"  ✓ agent_runs: {ticker} v{version} written "
                  f"(fy={fiscal_year}, git={git_sha or '—'})")
        return version

    def get_latest_agent_run(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Return the most recent agent run for a ticker, JSON columns
        decoded to dicts. Returns None if no row exists.

        Use this from API read paths to fetch LLM-authored content.
        Deterministic content (financials, DCF, capital stack) must NOT
        be sourced here — recompute from `company_records_latest` instead.
        """
        ticker = str(ticker).upper()
        row = self._conn.execute(
            "SELECT * FROM agent_runs_latest WHERE ticker = ?",
            [ticker],
        ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._conn.description]
        record = dict(zip(cols, row))
        for k in self._AGENT_RUN_PAYLOAD_KEYS:
            v = record.get(k)
            if v is not None and isinstance(v, str):
                try:
                    record[k] = json.loads(v)
                except json.JSONDecodeError:
                    record[k] = None
        return record

    # ─────────────────────────────────────────────────────────────────────────
    # Qualitative assessments — versioned 1-7 scores across analytical dimensions
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_qualitative_assessment(self, record) -> None:
        """Persist a qualitative assessment as a new versioned row.

        Args:
            record: An `aletheia.qualitative.types.AssessmentRecord` instance.

        Validates the record against the catalog before writing:
          - `dimension_id` must be a known catalog entry
          - `source_category` must match the catalog entry's declared category
          - `score`, when present, must be in [1.0, 7.0]
          - `narrative`, when present, must be ≤ 500 characters

        Validation failures raise ValueError. The catalog is loaded lazily
        to avoid a circular import at module-load time (database.py is a
        dependency of the qualitative package, not the other way around).
        """
        from aletheia.qualitative.types import AssessmentRecord, SourceCategory
        from config.qualitative_dimensions import DIMENSIONS

        if not isinstance(record, AssessmentRecord):
            raise TypeError(
                f"upsert_qualitative_assessment expected AssessmentRecord, "
                f"got {type(record).__name__}"
            )

        # Catalog membership check — prevents typos and stale-dimension writes
        if record.dimension_id not in DIMENSIONS:
            raise ValueError(
                f"upsert_qualitative_assessment: unknown dimension_id "
                f"{record.dimension_id!r}. Catalog known IDs: "
                f"{sorted(DIMENSIONS.keys())}"
            )

        # Source-category match — prevents writing a HITL submission against
        # a deterministic dimension or vice versa
        catalog_entry = DIMENSIONS[record.dimension_id]
        if record.source_category != catalog_entry.source_category:
            raise ValueError(
                f"upsert_qualitative_assessment: source_category mismatch "
                f"for {record.dimension_id!r}. Catalog declares "
                f"{catalog_entry.source_category.value}, record has "
                f"{record.source_category.value}."
            )

        # Score range check (PENDING_DATA may legitimately have score=None)
        if record.score is not None:
            if not (1.0 <= float(record.score) <= 7.0):
                raise ValueError(
                    f"upsert_qualitative_assessment: score {record.score} "
                    f"out of [1.0, 7.0] range for {record.dimension_id!r}"
                )

        # Narrative length check — protects DB column from runaway input
        if record.narrative is not None and len(record.narrative) > 500:
            raise ValueError(
                f"upsert_qualitative_assessment: narrative is "
                f"{len(record.narrative)} chars; max is 500."
            )

        self._conn.execute(
            """
            INSERT INTO qualitative_assessments
            (assessment_id, ticker, dimension_id, score, sub_scores, narrative,
             source_category, source_payload, assessed_at, analyst_id,
             code_git_sha, input_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record.assessment_id,
                record.ticker.upper(),
                record.dimension_id,
                record.score,
                json.dumps(record.sub_scores) if record.sub_scores is not None else None,
                record.narrative,
                record.source_category.value,
                json.dumps(record.source_payload, default=str),
                record.assessed_at,
                record.analyst_id,
                record.code_git_sha,
                record.input_fingerprint,
            ],
        )

        if self.verbose:
            print(f"  ✓ qualitative_assessments: {record.ticker} / "
                  f"{record.dimension_id} = {record.score} "
                  f"({record.source_category.value})")

    def get_latest_assessment(self, ticker: str, dimension_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest assessment for a (ticker, dimension_id) pair.

        Returns None if no assessment exists. JSON columns (`sub_scores`,
        `source_payload`) are decoded back to dicts.
        """
        row = self._conn.execute(
            "SELECT * FROM qualitative_assessments_latest "
            "WHERE ticker = ? AND dimension_id = ?",
            [ticker.upper(), dimension_id],
        ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._conn.description]
        record = dict(zip(cols, row))
        for k in ("sub_scores", "source_payload"):
            v = record.get(k)
            if v is not None and isinstance(v, str):
                try:
                    record[k] = json.loads(v)
                except json.JSONDecodeError:
                    record[k] = None
        return record

    def get_all_assessments_for_ticker(self, ticker: str) -> Dict[str, Dict[str, Any]]:
        """Return the latest assessment per dimension for a ticker, as a
        dict keyed by `dimension_id`. Dimensions without any assessment
        are NOT included — the caller cross-references against the
        catalog to determine empty states (`not_assessed` vs `pending_data`).
        """
        rows = self._conn.execute(
            "SELECT * FROM qualitative_assessments_latest WHERE ticker = ?",
            [ticker.upper()],
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            record = dict(zip(cols, row))
            for k in ("sub_scores", "source_payload"):
                v = record.get(k)
                if v is not None and isinstance(v, str):
                    try:
                        record[k] = json.loads(v)
                    except json.JSONDecodeError:
                        record[k] = None
            out[record["dimension_id"]] = record
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # Read operations
    # ─────────────────────────────────────────────────────────────────────────

    def query(self, sql: str, params: list = None) -> pd.DataFrame:
        """Execute any SQL query and return a DataFrame."""
        if params:
            return self._conn.execute(sql, params).fetchdf()
        return self._conn.execute(sql).fetchdf()

    def get_latest(self, ticker: str) -> pd.DataFrame:
        """Get the latest cleaned record for a ticker (all years)."""
        return self.query(
            "SELECT * FROM company_records_latest WHERE ticker = ? ORDER BY fiscal_year",
            [ticker]
        )

    def get_flags(self, ticker: str, fiscal_year: int = None) -> pd.DataFrame:
        """Get all cleaning flags for a ticker."""
        if fiscal_year:
            return self.query(
                "SELECT * FROM cleaning_flags WHERE ticker = ? AND fiscal_year = ? ORDER BY domain",
                [ticker, fiscal_year]
            )
        return self.query(
            "SELECT * FROM cleaning_flags WHERE ticker = ? ORDER BY fiscal_year, domain",
            [ticker]
        )

    def get_flagged_for_review(self) -> pd.DataFrame:
        """Get all companies currently flagged for review."""
        return self.query("SELECT * FROM flagged_for_review")

    def get_portfolio_summary(self) -> pd.DataFrame:
        """Get portfolio-level quality summary."""
        return self.query("SELECT * FROM portfolio_quality_summary")

    def parquet_query(self, ticker: str) -> pd.DataFrame:
        """
        Query the raw canonical Parquet directly — no cleaning applied.
        Useful for inspection and debugging.
        """
        parquet_path = f"valuation_data/canonical/financials/{ticker.upper()}.parquet"
        return self.query(f"SELECT * FROM read_parquet('{parquet_path}')")

    def summary(self) -> str:
        """Print a summary of database contents."""
        records = self._conn.execute("SELECT COUNT(*) FROM company_records").fetchone()[0]
        tickers = self._conn.execute("SELECT COUNT(DISTINCT ticker) FROM company_records").fetchone()[0]
        flags = self._conn.execute("SELECT COUNT(*) FROM cleaning_flags").fetchone()[0]
        flagged = self._conn.execute(
            "SELECT COUNT(*) FROM screen_results WHERE any_flagged = TRUE"
        ).fetchone()[0]

        return (
            f"InvestmentDatabase: {self.db_path}\n"
            f"  Records  : {records} ({tickers} companies)\n"
            f"  Flags    : {flags} cleaning flags\n"
            f"  Flagged  : {flagged} screen results requiring review"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline runner — wires everything together
# ─────────────────────────────────────────────────────────────────────────────

def process_ticker(
    ticker: str,
    fiscal_year: int,
    engine: CleaningEngine,
    screens: QuantitativeScreens,
    prior_record: Optional[CleanedRecord] = None,
    current_price: Optional[float] = None,
    wacc_override: Optional[float] = None
) -> CalculationOutput:
    """
    Pure calculation function. Processes a single ticker and fiscal year.
    No DB interaction. Returns explicitly formatted CalculationOutput.
    """
    try:
        # Clean record
        record = engine.clean(ticker, fiscal_year, prior_year_record=prior_record)
        
        # Phase 2 Validation Gate
        val_result = IngestionValidator.validate(record)

        if not val_result.is_valid:
            status = "validation_failed"
            # Format structured failures into a readable string for error_detail
            error_details = []
            for f in val_result.failures:
                error_details.append(f"{f.field}: {f.reason}")
            error_detail = "; ".join(error_details)
            return CalculationOutput(ticker, fiscal_year, status, record, None, error_detail)

        # In this codebase, validation/missing fields logic inside engine sets blocking_errors
        if record.blocking_errors:
            status = "validation_failed"
            error_detail = "; ".join(record.blocking_errors)
            return CalculationOutput(ticker, fiscal_year, status, record, None, error_detail)

        # Run screens
        screen_result = screens.run_all(
            record,
            prior_record=prior_record,
            current_price=current_price,
            wacc_override=wacc_override,
        )

        return CalculationOutput(ticker, fiscal_year, "success", record, screen_result, None)

    except IngestionError as e:
        # Expected operational errors
        if isinstance(e, MissingFieldError):
            status = "missing_fields"
        elif isinstance(e, ValidationError):
            status = "validation_failed"
        else:
            status = "error"
        return CalculationOutput(ticker, fiscal_year, status, None, None, str(e))
    # Note: We do NOT catch Exception (e.g. KeyError, AttributeError). They are bugs and will crash the process.


def run_pipeline(
    tickers: List[str],
    db: InvestmentDatabase = None,
    canonical_dir: str = "valuation_data/canonical/financials",
    current_prices: Dict[str, float] = None,
    wacc_estimates: Dict[str, float] = None,
    verbose: bool = True,
) -> Dict[str, List[CalculationOutput]]:
    """
    Dumb orchestrator for the Phase 1 pipeline.
    Iterates over tickers, determines fiscal years, calls process_ticker,
    and commits storage per-ticker immediately upon success.
    """
    if db is None:
        db = InvestmentDatabase(verbose=verbose)

    engine = CleaningEngine(canonical_dir=canonical_dir, verbose=verbose)
    screens = QuantitativeScreens(verbose=verbose)

    all_outputs = {}
    prices = current_prices or {}
    waccs = wacc_estimates or {}

    for ticker in tickers:
        if verbose:
            print(f"\n{'#'*60}")
            print(f"  Processing: {ticker}")
            print(f"{'#'*60}")

        # For sequential processing, we still need to figure out which years exist 
        # and process them in order to pass the prior_record properly.
        # This belongs in the orchestrator.
        parquet_path = Path(canonical_dir) / f"{ticker.upper()}.parquet"
        years = set()
        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path)
                if "fy" in df.columns:
                    years.update(df["fy"].dropna().unique().astype(int).tolist())
            except Exception:
                pass
        
        # Also check raw facts for years
        raw_facts = engine._load_raw_facts(ticker)
        if raw_facts:
            us_gaap = raw_facts.get("facts", {}).get("us-gaap", {})
            ifrs = raw_facts.get("facts", {}).get("ifrs-full", {})
            all_facts = {}
            all_facts.update(ifrs)
            all_facts.update(us_gaap)
            for concept in all_facts.values():
                for unit_type, units in concept.get("units", {}).items():
                    if unit_type not in ("USD", "shares", "pure", "EUR", "TWD", "CAD", "GBP", "JPY", "CHF"):
                        continue
                    for unit in units:
                        if unit.get("form") in ("10-K", "20-F", "40-F") and unit.get("fy"):
                            years.add(int(unit["fy"]))

        if not years:
            if verbose:
                print(f"  ⚠ No data found for {ticker}")
            continue

        years = sorted(list(years))
        ticker_outputs = []
        prior = None

        for fy in years:
            try:
                # Call pure calculation function
                output = process_ticker(
                    ticker, int(fy), engine, screens,
                    prior_record=prior,
                    current_price=prices.get(ticker),
                    wacc_override=waccs.get(ticker)
                )
                ticker_outputs.append(output)

                if output.status == "success" and output.cleaned_record and output.screen_result:
                    # Commit storage per ticker immediately upon success
                    db.upsert_record(output.cleaned_record)
                    db.upsert_screens(output.screen_result)
                    prior = output.cleaned_record
                else:
                    # Quarantine semantics: absent from main table.
                    # Handled natively by NOT calling upsert_record.
                    # We can log or write to quarantine table here.
                    if verbose:
                        print(f"  ⚠ {ticker} FY{fy} failed with status '{output.status}': {output.error_detail}")
                    # Prior record remains whatever it was (or None)
                    prior = None
                    
            except IngestionError as e:
                # Only catch IngestionError to prevent a single ticker from crashing the run.
                # Code bugs like KeyError will crash the orchestrator loudly.
                if verbose:
                    print(f"  ⚠ {ticker} FY{fy} raised IngestionError: {str(e)}")
                output = CalculationOutput(ticker, int(fy), "error", None, None, str(e))
                ticker_outputs.append(output)
                prior = None

        all_outputs[ticker] = ticker_outputs

    if verbose:
        print(f"\n{'='*60}")
        print(db.summary())

    return all_outputs


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import csv

    # Load universe
    universe_path = Path("config/universe.csv")
    tickers = []
    if universe_path.exists():
        with open(universe_path) as f:
            reader = csv.DictReader(f)
            tickers = [row["ticker"] for row in reader]
    
    # Allow CLI override
    if len(sys.argv) > 1:
        tickers = [t.upper() for t in sys.argv[1:]]

    if not tickers:
        print("Usage: python3 aletheia/data/database.py [TICKER ...]")
        print("       or configure config/universe.csv")
        sys.exit(1)

    print(f"Running Phase 1 pipeline for: {tickers}")

    with InvestmentDatabase() as db:
        results = run_pipeline(tickers, db=db, verbose=True)
        print("\n" + db.summary())

        # Show flagged companies
        flagged = db.get_flagged_for_review()
        if not flagged.empty:
            print("\n⚠ Companies requiring review:")
            print(flagged[["ticker", "fiscal_year", "beneish_m_score",
                           "sloan_accrual_ratio", "overall_quality_score"]].to_string())
        else:
            print("\n✓ No companies flagged for review")
