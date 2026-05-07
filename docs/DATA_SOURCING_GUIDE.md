# Data Sourcing & Cross-Source Differences Guide

When you read a number in Aletheia and notice it differs from Yahoo, FMP,
Schwab, or the company's own annual report, this guide explains why. The
short answer is almost always one of:

1. **Aggregation level** — different sources roll up granular XBRL fields
   into single labels. Aletheia keeps the granular fields; vendors pre-sum
   them.
2. **Methodology choice** — definitions like EBITDA, Free Cash Flow, and
   "Short-Term Debt" have multiple defensible definitions. Aletheia and
   each vendor pick one.
3. **Schema mismatch** — banks, insurers, utilities, and foreign filers
   structure their statements differently. A vendor's "Revenue" column for
   an insurer may not equal the issuer's tagged us-gaap:Revenues.

Aletheia's source of truth is **SEC XBRL** — the exact tagged values the
issuer filed in its 10-K, byte-for-byte. Vendor numbers (FMP, Yahoo) are
normalized layers on top of that truth. When they diverge, our number is
faithful to the filing; the vendor is faithful to its normalization.

---

## The two validators we run

| Validator | Source | Covers | What it tests |
|---|---|---|---|
| `scripts/validate_sec.py` | SEC XBRL companyfacts (every SEC filer, free, authoritative) | All 25 tickers | Byte-perfect agreement vs the issuer's filed canonical us-gaap tag |
| `scripts/validate_fmp.py` | FinancialModelingPrep API | 23 of 25 tickers (current FMP plan covers the full US universe; only foreign filers TSM and ASML are skipped on currency mismatch) | Whether our numbers reconcile to FMP's normalized statements after accounting for documented aggregation/methodology differences |

Both reports are regenerated on demand and live in `docs/`:
[SEC_VALIDATION_REPORT.md](SEC_VALIDATION_REPORT.md) and
[FMP_VALIDATION_REPORT.md](FMP_VALIDATION_REPORT.md). For analyst-facing
context on Schwab cross-checks see
[SCHWAB_VALIDATION_REPORT.md](SCHWAB_VALIDATION_REPORT.md).

---

## What's externally validated

The two automated validators cover **45 fields per ticker** combined:
8 SEC bottom-line raw fields + 28 FMP statement lines + 6 derived ratios
+ 11 screening-engine ratios. The screening section validates end-to-end
the metrics the dashboard's screening tab displays — P/E, P/B, EV/EBITDA,
EV/FCF, Debt-to-Equity, Current Ratio, Interest Coverage, Net Debt /
EBITDA, Dividend Yield, EV, and Market Cap.

**Robust to definitional choice (always byte-perfect or within ~1%):**
Gross Margin, EBIT Margin, ROE. These reconcile across every ticker FMP
covers — same numerator and denominator regardless of methodology school.

**Sensitive to definitional choice (drift documents the choice):** EBITDA
Margin (SBC treatment), ROIC (operating-side vs financing-side
denominator), Invested Capital (same root cause as ROIC).

NOPAT, FCFF, and NetDebt are still not externally validated — FMP doesn't
expose comparable named ratios for them. They are computed as deterministic
functions of validated inputs, so consistency-checking them would be useful
but not high-priority.

## EBITDA — which version when

We expose two:

- **`clean_EBITDA`** = Operating Income + D&A. The conventional definition.
  This is what the calc engine uses for DCF, screening multiples, and any
  margin metric. **Use this by default.**
- **`clean_EBITDA_ExcludingSBC`** = `clean_EBITDA` + Stock-Based
  Compensation. Follows FMP's convention; treats SBC as a non-cash addback.
  Use this when:
  - You're comparing to peers quoted on a SaaS-EBITDA / "operating cash
    EBITDA" basis (common in software and growth-tech research notes).
  - You're cross-checking against an FMP, S&P Capital IQ, or sell-side
    table where the column header silently uses the SBC-addback variant.
  - You're computing a multiple where the comparable transaction price
    was set on this basis.

**Don't** use `clean_EBITDA_ExcludingSBC` for valuation discounting,
covenant testing, or any cash-flow-based work. SBC is a real economic cost
borne by existing shareholders through dilution; treating it as a non-cash
item double-counts when the share count grows.

GOOGL FY2025 illustrates the spread:
- `clean_EBITDA` $150.7B (calc-engine default)
- `clean_EBITDA_ExcludingSBC` $177.8B (matches FMP within 1.2%)
- The $27B gap is exactly Alphabet's FY2025 stock-based comp.

---

## SG&A — granular vs combined

Different filers split SG&A differently. Three common patterns:

| Filer pattern | Example | What XBRL contains |
|---|---|---|
| Rolled-up only | LLY, AAPL | `SellingGeneralAndAdministrativeExpense` (single number) |
| Components only | MSFT, META | `SellingAndMarketingExpense` + `GeneralAndAdministrativeExpense` separately |
| Marketing under its own tag | AMZN, V | `MarketingExpense` (or `MarketingAndAdvertisingExpense`) + `GeneralAndAdministrativeExpense` |

To support all three, Aletheia exposes:

- **`clean_GeneralAndAdministrative`** — G&A only, every filer that breaks
  it out
- **`clean_SellingAndMarketing`** — selling/marketing component, where
  filed under that tag (or under `MarketingExpense` for AMZN-style filers)
- **`clean_SGA_Combined`** — G&A + Selling/Marketing, computed
  consistently across all filers regardless of how they split it. Use this
  for cross-ticker SG&A-as-percent-of-revenue comparisons.

The legacy `raw_SG&A` field is preserved but is *not* cross-ticker
comparable on its own — for AMZN it captures only G&A, missing the $47B
marketing line. Prefer `clean_SGA_Combined` for screening.

---

## Why our number sometimes differs from FMP

Every >5% drift we have ever observed in FMP-validated tickers reconciles
to one of these documented patterns. None are bugs in Aletheia.

### Receivables aggregation
**Where it shows up:** AAPL `Accounts Receivable` reads $39.78B in
Aletheia, $72.96B in FMP.

**Why:** FMP's `netReceivables` aggregates trade AR with vendor non-trade
receivables and other receivables. Aletheia keeps trade AR isolated.
$39.78B + $33.18B (vendor non-trade) = $72.96B exactly.

**Which to trust:** if you're modeling working-capital cycle, trade AR is
the right number. If you're matching a sell-side comp screen that uses
"Accounts Receivable", FMP's broader number is what they're showing.

### Short-term debt aggregation
**Where it shows up:** AAPL `Short-Term Debt` reads $7.98B in Aletheia,
$20.33B in FMP.

**Why:** FMP combines commercial paper with the current portion of
long-term debt. Aletheia keeps `ShortTermDebt` ($7.98B) and
`CurrentPortionLongTermDebt` ($12.35B) separate. The sum equals FMP's number
exactly.

**Which to trust:** for refinancing risk analysis, the split matters
(maturing-within-12-months bonds need refinancing planning that commercial
paper rolls don't). For a quick "current debt" screen, FMP's combined
number is fine.

### PPE includes operating-lease ROU
**Where it shows up:** Applies to MSFT, COST, GOOGL, AMZN, NVDA, WMT —
roughly any tenant or capex-heavy filer. Drifts of -5% to -19%.

**Why:** FMP's `propertyPlantEquipmentNet` includes the
`Operating Lease Right-of-Use Asset`. Aletheia keeps PPE and ROU separate.

**Reconciliation, byte-perfect on every observed case:**
- MSFT FY2025: FMP − Aletheia = $24.82B = `ROU_Asset_Operating` $24.82B
- COST FY2025: FMP − Aletheia = $2.73B = `ROU_Asset_Operating` $2.73B
- GOOGL FY2025: FMP − Aletheia = $15.22B = `ROU_Asset_Operating` $15.22B
- AMZN FY2025: FMP − Aletheia = $86.05B = `ROU_Asset_Operating` $86.05B
- NVDA FY2026: FMP − Aletheia = $2.87B = `ROU_Asset_Operating`
- WMT FY2026: FMP − Aletheia = $20.88B = `ROU_Asset_Operating`

The pattern holds with no leakage. AMZN's larger absolute drift (-19%)
is because AMZN's lease base (fulfillment + AWS data centers) is larger,
not because the bundling rule differs.

**Which to trust:** if you're computing PPE turnover or a tangible-assets
ratio, exclude ROU. If you're matching a balance-sheet screen that uses
"PP&E", expect FMP's broader number.

### EBITDA ± stock-based compensation
**Where it shows up:** Most tickers, drifts of -5% to -16%.

**Why:** FMP's `ebitda` adds SBC back. Aletheia's `clean_EBITDA` follows
the conventional Operating Income + D&A. See the EBITDA section above —
use `clean_EBITDA_ExcludingSBC` if you need the FMP-comparable number.

### SG&A label scope
**Where it shows up:** AMZN Aletheia $11.17B vs FMP $58.30B (-81%);
V $1.93B vs $4.37B (-56%).

**Why:** FMP's `sellingGeneralAndAdministrativeExpenses` aggregates G&A,
selling/marketing, and sometimes professional fees under one label. Use
`clean_SGA_Combined` for the cross-ticker comparable equivalent. Operating
Income still matches in both — this is a labeling difference, not a
calculation bug.

### Total Equity / NCI treatment
**Where it shows up:** UNH +6.35%, WMT +6.29%.

**Why:** When the issuer files
`StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`
distinct from the parent-only `StockholdersEquity`, Aletheia and FMP can
land on opposite sides of NCI. Look at `raw_NoncontrollingInterest` to
quantify.

### ROIC and Invested Capital — operating-side vs financing-side
**Where it shows up:** Pretty much every ticker. FMP's ROIC reads 0.52 for
AAPL, ours reads 0.79; FMP's `investedCapital` reads $32B for AAPL, ours
reads $132B. Drifts of +30% to +60% on ROIC are typical.

**Why:** Two equally-standard definitions of "capital invested":

- **FMP — operating-side**: Invested Capital = Net Working Capital + Net
  PP&E. Measures the operating asset base actually deployed in production.
  AAPL FY2025: ($148B current assets − $166B current liabilities) + $50B
  PP&E = $32B. Reconciles exactly.
- **Aletheia — financing-side**: Invested Capital = Total Equity + Total
  Debt − Cash. Measures the capital investors have provided net of cash on
  hand. AAPL FY2025: $74B equity + $98B debt − $36B cash = $136B.
  Reconciles exactly.

**Which to trust:** for a DCF where ROIC gets compared to WACC, the
financing-side definition (ours) is more apt — both numerator and
denominator are at the firm-financing level. For asset-turnover analysis
or operational benchmarking against industry peers, the operating-side
definition (FMP's) is what most equity research desks use. Use whichever
matches the comp set you're comparing against; just don't mix.

### Buybacks — gross repurchases vs. net of issuance
**Where it shows up:** JPM Aletheia $31.59B vs FMP $34.59B (-8.67%).

**Why:** FMP's `commonStockRepurchased` reports gross share repurchases.
Aletheia reports net of new common-stock issuance. JPM FY2025: $34.59B
gross repurchases − $3.00B common stock issuance (RSU vesting, ESPP, etc.)
= $31.59B net. Reconciles exactly.

**Which to trust:** for capital-return-yield analysis (buyback yield as a
component of total shareholder return), you want gross repurchases — the
issuance is mostly compensation-driven and not an economic offset to
buybacks for existing holders. For change-in-share-count modelling, the
net figure is more apt.

### Buybacks — FMP under-reporting on Visa
**Where it shows up:** V Aletheia $18.32B vs FMP $13.39B (+36.8%).

**Why:** Aletheia's $18.32B equals Visa's SEC-tagged
`PaymentsForRepurchaseOfCommonStock` exactly. FMP's $13.39B is
partial — about $4.93B of repurchase activity (likely covering Class
B/C share conversions and tax-withholding repurchases on RSU vests) is
not captured in their `commonStockRepurchased` field for V.

**Which to trust:** ours, because it matches SEC byte-for-byte. Buyback
yield is a Buffett-relevant capital-return metric; using FMP's
under-reported number for V would understate the yield by ~30%.

### Bank, insurer, utility ROIC/Invested Capital — suppressed entirely
**Where it shows up:** JPM, BRK-B, NEE, UNH, CNC.

**Why:** ROIC and Invested Capital are operating-business ratios that
don't translate to bank, insurer, or regulated-utility balance sheets.
FMP produces values regardless (sometimes negative invested capital for
banks, like JPM's −$1,606B), but they aren't analytically meaningful.

**What we do:** the validator emits `n/a (schema)` for these rows
rather than misleading numerics. Tickers are routed by
`business_model != fcff_compatible` in
[config/ticker_classification.py](../config/ticker_classification.py).

**What you should look at instead:**
- Banks (JPM): efficiency ratio, NIM, ROTCE, CET1
- Insurers (UNH, CNC): combined ratio, premium growth, loss ratio
- Utilities (NEE): rate base, allowed ROE, capex intensity
- Conglomerates (BRK-B): segment-level analysis (insurance underwriting,
  railroad operations, utilities, equity portfolio mark-to-market)

These metrics are not yet validated by an automated framework — separate
validators per schema would close that gap.

### Price-timing on multiples (P/E, P/B, EV/EBITDA, EV/FCF, EV, Market Cap, Dividend Yield)
**Where it shows up:** essentially every ticker, drifts of +/- 5–15%.

**Why:** FMP's `/ratios` and `/key-metrics` endpoints lock the share price
to the *fiscal year close* (e.g., 2025-09-27 for AAPL FY2025). Our
screening engine uses *today's* market price, which has moved since FY
end. The drift on these metrics is therefore the share-price move from
period-end to now, not a calculation error.

**Reconciliation example:** AAPL FY2025 marketCap $3,818.74B (FMP, at
close 2025-09-27) vs $4,222.76B (ours, current price) = +10.58% — the
exact price appreciation since fiscal-year close.

**Which to trust:** for *backward-looking* analysis (was AAPL cheap at
FY-end?), FMP's number is right. For *forward-looking* multiple analysis
("at today's price, what am I paying?"), ours is right. They answer
different questions.

### Net Debt — marketable securities treatment
**Where it shows up:** AAPL Net Debt / EBITDA = +0.53 (FMP) vs −0.22
(ours), sign flip.

**Why:** Aletheia subtracts long-term marketable securities from gross
debt when computing Net Debt — AAPL's $77B securities portfolio is treated
as accessible-cash-equivalent (you can liquidate it to pay debt). FMP
treats only cash and short-term investments as offsets. Cash-rich tech
tickers can therefore flip from net-cash (ours) to net-debt (FMP).

**Which to trust:** for enterprise-value calculations and acquisition
analysis, ours is more accurate (a buyer pays for the securities portfolio
and uses it to retire debt). For pure leverage screening, FMP's narrower
definition is more conservative.

### AMZN Free Cash Flow follows AMZN's IR convention
**Where it shows up:** AMZN Aletheia $6.14B vs FMP $7.70B.

**Why:** Amazon's annual report defines FCF as Operating CF − CapEx −
Finance Lease Principal Payments. We follow that definition. FMP uses the
simple Operating CF − CapEx. The $1.56B gap is exactly
`FinanceLeasePrincipalPayments`.

**Which to trust:** Amazon's own narrative IR communications cite the
adjusted version, so for matching their "FCF" headline, ours is correct.
For peer comparisons that use the simple definition, FMP's is the right
basis.

---

## Coverage gaps and how to read them

| Source | Subscription-restricted on free tier | Currency-mismatched | Schema-mismatched |
|---|---|---|---|
| FMP | none on current plan | TSM (TWD), ASML (EUR) | JPM (bank), BRK-B (conglomerate), UNH/CNC (insurer), NEE (utility) — for ROIC/IC only |
| SEC XBRL | none | ASML (EUR), TSM (TWD) — values exist but not in USD units | none |
| Schwab (manual) | none | none — but only 7 tickers cross-checked | none |

If a ticker shows ✗ on one source, the other usually closes the gap. The
only ticker not yet covered by an automated validator on at least one
source is TSM (foreign currency on both FMP and SEC USD-only paths). Schwab
manual cross-check is the fallback there.

---

## Bank caveat — JPM (and any other bank in future)

Banks file cash flow statements where huge trading-securities movements,
loans-held-for-sale flows, and deposit movements get classified
differently across vendors. JPM FY2025 OCF in raw XBRL is **−$147.78B**;
FMP normalizes it to +$100.87B. JPM's OCF year-over-year is also wildly
volatile in raw form (FY2020: −$80B, FY2022: +$107B, FY2024: −$42B). For
banks, OCF and FCF are not the right primary lenses regardless of source —
prefer Net Interest Income, efficiency ratio, and ROTCE.

---

## Known gaps in current validation

These are real validation gaps with known shapes. Each has a remediation
path; none are systemic blockers.

1. **Single-fiscal-year coverage.** Both validators check the latest FY
   only. A multi-year extension would catch tag-mapping regressions when
   issuers restate or change tag conventions, but adds substantial work
   (more cells × more time-windowing logic).
2. ~~Subscription-restricted tickers~~ — **Closed.** The current FMP plan
   covers the full US universe. The previous 11-ticker gap (LLY, ASML,
   SMCI, ABT, BRK-B, CAT, CNC, NEE, ORCL, QCOM, TXN) is gone — all are
   now FMP-validated except ASML (EUR currency, separate gap).
3. **Foreign filers.** TSM is correctly skipped on currency mismatch in
   FMP; ASML's USD-translated SEC XBRL is sparse. A separate validation
   path is needed — either parsing 20-F filings or using USD-translated
   investor-presentation tables.
4. **Schema-specific frameworks for banks, insurers, utilities,
   conglomerates.** The current FMP validator suppresses ROIC/InvestedCapital
   for these tickers but doesn't *replace* them with the metrics that
   actually matter. Each schema needs a dedicated validator: efficiency
   ratio + NIM + ROTCE for banks, combined ratio + loss ratio + premium
   growth for insurers, rate base + allowed ROE for utilities, segment
   underwriting for conglomerates.

## How to debug a number that surprises you

1. Open `docs/SEC_VALIDATION_REPORT.md` and find the ticker. If the field
   is ✓, our raw value matches the SEC filing exactly — the surprise is
   in the issuer's filing, not in our pipeline.
2. If the field is ✗, our tag resolver picked the wrong variant. Open
   `valuation_data/raw/sec/companyfacts/CIK*.json` and search for the
   tag the report cites; you'll see the alternative tags the issuer files.
3. If you're comparing to FMP/Yahoo and they disagree with us, check
   `docs/FMP_VALIDATION_REPORT.md` — the relevant pattern is named in the
   findings table at the top of that report.
4. If you're comparing to Schwab, check `docs/SCHWAB_VALIDATION_REPORT.md`
   for the 7 tickers cross-checked there.
5. If none of the above explains it, open an audit trace:
   `audits/trace_<TICKER>_*.json` records every cleaning transform applied,
   so you can see field-by-field where your value came from.
