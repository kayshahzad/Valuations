"""
aletheia/scenarios/persistence.py

Save analyst-constructed scenarios per ticker as JSON files in
`valuation_data/scenarios/<TICKER>/<slug>__<YYYY-MM-DD>.json`. Reload and
re-run any saved scenario; the saved ScenarioOverride is what runs, not
current defaults — so historical scenarios remain reproducible even after
parameter calibration changes.

Storage choice: JSON over DuckDB. Analyst-curated low-volume data benefits
from human-readable storage and version control. DuckDB is overkill here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from aletheia.contracts.interfaces import ScenarioOverride


_SCENARIOS_DIR = Path("valuation_data/scenarios")


@dataclass(frozen=True)
class SavedScenario:
    """An analyst-saved scenario. Immutable after creation."""
    ticker: str
    name: str
    rationale: str
    created_at: date
    override: ScenarioOverride

    def slug(self) -> str:
        """Filesystem-safe slug from the scenario name."""
        s = re.sub(r"[^a-zA-Z0-9]+", "_", self.name).strip("_").lower()
        return s[:60] or "scenario"

    def filename(self) -> str:
        return f"{self.slug()}__{self.created_at.isoformat()}.json"


def _ticker_dir(ticker: str) -> Path:
    return _SCENARIOS_DIR / ticker.upper()


def save_scenario(s: SavedScenario, base_dir: Optional[Path] = None) -> Path:
    """Persist a SavedScenario to disk; returns the path written."""
    target_dir = (base_dir or _SCENARIOS_DIR) / s.ticker.upper()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / s.filename()
    payload = {
        "ticker":      s.ticker,
        "name":        s.name,
        "rationale":   s.rationale,
        "created_at":  s.created_at.isoformat(),
        "override":    s.override.model_dump(mode="json", exclude_none=True),
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    return path


def load_scenario(path: Path) -> SavedScenario:
    """Read a saved-scenario JSON file. Validates the override on load."""
    with open(path) as f:
        data = json.load(f)
    override = ScenarioOverride.model_validate(data["override"])
    return SavedScenario(
        ticker=data["ticker"],
        name=data["name"],
        rationale=data["rationale"],
        created_at=date.fromisoformat(data["created_at"]),
        override=override,
    )


def list_scenarios(
    ticker: str,
    base_dir: Optional[Path] = None,
) -> List[SavedScenario]:
    """Return all saved scenarios for a ticker, sorted by created_at desc."""
    target_dir = (base_dir or _SCENARIOS_DIR) / ticker.upper()
    if not target_dir.exists():
        return []
    out: List[SavedScenario] = []
    for path in sorted(target_dir.glob("*.json")):
        try:
            out.append(load_scenario(path))
        except Exception as e:
            # Skip corrupted files but don't fail the whole listing
            print(f"[WARN] failed to load {path}: {e}")
    return sorted(out, key=lambda s: s.created_at, reverse=True)


def run_saved(s: SavedScenario):
    """Convenience: re-run a saved scenario via the library runner."""
    from aletheia.scenarios.library import run_scenario, ScenarioRunResult
    from aletheia.contracts.interfaces import CalculationInput

    # Wrap the saved override as a callable matching ScenarioFn signature
    def _fn(_calc):
        # Return the saved override + minimal metadata
        return s.override, {"saved_name": s.name, "created_at": s.created_at.isoformat()}

    return run_scenario(s.ticker, _fn)
