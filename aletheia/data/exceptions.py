"""
aletheia/data/exceptions.py

Explicit exception hierarchy for the data ingestion and calculation pipeline.
Uncaught exceptions that do not inherit from IngestionError (e.g. KeyError, AttributeError)
are considered code bugs and should intentionally crash the process.
"""

from typing import Any

class IngestionError(Exception):
    """Base class for all expected operational errors during ingestion."""
    pass

class MissingFieldError(IngestionError):
    """Raised when a critical field required for calculation is missing from the raw or cleaned data."""
    def __init__(self, field: str, reason: str = "missing_field", message: str = ""):
        self.message = message or f"Missing field: {field}"
        super().__init__(self.message)
        self.field = field
        self.reason = reason

class ValidationError(IngestionError):
    """Raised when a record fails critical cross-checks or numeric tolerance bounds."""
    def __init__(self, field: str, reason: str, expected: Any = None, actual: Any = None, message: str = ""):
        self.message = message or f"Validation failed for {field}: {reason}"
        super().__init__(self.message)
        self.field = field
        self.reason = reason
        self.expected = expected
        self.actual = actual

class SourceFetchError(IngestionError):
    """Raised when underlying source data (like Parquet or XBRL json) cannot be retrieved or parsed."""
    pass
