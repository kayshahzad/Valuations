"""
Batch DCF runner for the full universe — no LLMs.
Uses the same _make_calc_input fixture from tests/calculation_layer/conftest.py.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "calculation_layer"))
from conftest import _make_calc_input  # type: ignore

from config.ticker_classification import UNIVERSE
from aletheia.tools.dcf_engine import DCFEngine


def main() -> int:
    engine = DCFEngine(verbose=False)
    rows = []
    n_ok = n_skip = n_err = 0

    for ticker in sorted(UNIVERSE.keys()):
        try:
            calc_input = _make_calc_input(ticker)
            result = engine.run(calc_input)
            base = result.base
            ev = base.enterprise_value
            ips = result.intrinsic_per_share(ev, result.net_debt)
            rows.append((ticker, "OK", f"EV={ev:,.0f}", f"IPS={ips:,.2f}",
                         f"TV={base.terminal.tv_used:,.0f}",
                         f"warn={len(result.warnings)}"))
            n_ok += 1
        except NotImplementedError as e:
            rows.append((ticker, "SKIP", str(e)[:80], "", "", ""))
            n_skip += 1
        except Exception as e:
            rows.append((ticker, "ERR", f"{type(e).__name__}: {str(e)[:80]}", "", "", ""))
            n_err += 1

    print(f"\n{'TICKER':<8}{'STATUS':<6}{'COL3':<32}{'COL4':<22}{'COL5':<22}{'COL6'}")
    print("-" * 110)
    for r in rows:
        print(f"{r[0]:<8}{r[1]:<6}{r[2]:<32}{r[3]:<22}{r[4]:<22}{r[5]}")
    print("-" * 110)
    print(f"OK={n_ok}  SKIP={n_skip}  ERR={n_err}  TOTAL={len(rows)}")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
