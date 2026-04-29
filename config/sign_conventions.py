"""
sign_conventions.py

Defines the sign normalization rules for canonical fields.
Fields not listed here default to 'signed' (keep their original sign).
'abs' ensures that cash outflows are represented as positive magnitudes,
which allows `max()` priority resolution logic to work deterministically.
"""

SIGN_CONVENTIONS = {
    "CapEx": "abs",
    "Buybacks": "abs",
    "CashTaxesPaid": "abs",
    "FinanceLeasePrincipalPayments": "abs",
    "RepaymentsOfLongTermCapitalLeaseObligations": "abs",
    "MedicalClaims": "abs",
    "COGS": "abs",
    "SG&A": "abs",
    "R&D": "abs",
    "OperatingExpenses": "abs",
    "InterestExpense": "abs",
    "TaxExpense": "abs"
}

def get_sign_convention(field: str) -> str:
    """Returns 'abs' or 'signed' for a given canonical field."""
    return SIGN_CONVENTIONS.get(field, "signed")
