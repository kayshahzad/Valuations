"""Overlay persisted serving-state (analyst edits) onto the local serving DB.

Run at container startup AFTER the base DB is copied to /tmp (DUCKDB_PATH) so
DCF overrides + current-state acknowledgments survive restart / scale-to-zero.
Non-fatal: a missing sidecar or any error just leaves the base-DB tables as-is.

  python -m aletheia.serving.restore_state
"""
from aletheia.data.database import InvestmentDatabase


def main() -> int:
    try:
        db = InvestmentDatabase(verbose=True)
    except Exception as exc:  # pragma: no cover
        print(f"restore_state: could not open DB ({exc}); skipping")
        return 0
    try:
        db.restore_serving_state()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
