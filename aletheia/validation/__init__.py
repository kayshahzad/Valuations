"""Isolated validation harness for Stage 3 calculations.

Feeds Stage 3 with FMP-sourced inputs (no SEC XBRL, no DuckDB cleaning
records) to validate the calc layer independently of the data layer.

Entry point: ``run_stage3_isolated(ticker)`` in ``stage3_isolated``.
"""
