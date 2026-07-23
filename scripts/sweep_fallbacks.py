#!/usr/bin/env python3
"""Phase-0 fallback-site sweep (fix-plan task 0.2.1).

Statically enumerates EVERY falsy-fallback substitution across the calc/data
surface — the sites where a legitimate 0/None can become a fabricated constant
(`x or 0.21`, `.get(k) or 1.0`, `a.get(k) or b.get(k)` accessor chains). AST-based
so line numbers, enclosing function, and the fallback constant are exact.

Emits a machine list (JSON) + a markdown skeleton with an unfilled `criticality`
column for the 0.2.2 annotation pass.

    python scripts/sweep_fallbacks.py            # -> docs/fallback_sites.{json,md}
"""
from __future__ import annotations

import argparse
import ast
import json
import linecache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["aletheia/data", "aletheia/calculations"]


class _Visitor(ast.NodeVisitor):
    def __init__(self, relpath: str):
        self.rel = relpath
        self.func_stack: list[str] = []
        self.sites: list[dict] = []

    def visit_FunctionDef(self, node):
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _is_get_call(self, n) -> bool:
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get")

    def visit_BoolOp(self, node):
        if isinstance(node.op, ast.Or):
            const, kind = None, None
            for v in node.values:
                # numeric literal fallback: `... or 0.21`
                if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) \
                        and not isinstance(v.value, bool):
                    const, kind = v.value, "numeric_fallback"
            if kind is None and any(self._is_get_call(v) for v in node.values):
                kind = "accessor_chain"
            if kind is not None:
                ln = node.lineno
                self.sites.append({
                    "file": self.rel,
                    "line": ln,
                    "function": self.func_stack[-1] if self.func_stack else "<module>",
                    "kind": kind,
                    "const": const,
                    "source": linecache.getline(str(ROOT / self.rel), ln).strip()[:140],
                })
        self.generic_visit(node)


def sweep() -> list[dict]:
    sites: list[dict] = []
    for d in SCAN_DIRS:
        for py in sorted((ROOT / d).rglob("*.py")):
            rel = str(py.relative_to(ROOT))
            try:
                tree = ast.parse(py.read_text(), filename=rel)
            except SyntaxError:
                continue
            v = _Visitor(rel)
            v.visit(tree)
            sites.extend(v.sites)
    sites.sort(key=lambda s: (s["file"], s["line"]))
    return sites


# Heuristic first-pass criticality. DRAFT ONLY — the 0.2.2 gate still requires
# human confirmation, but this isolates the HOT set that scopes Phase 1.
_HOT_TERMS = ("nopat", "wacc", "roe", "roic", "tax", "equity", "revenue", "ebit",
              "ebitda", "capex", "cost_of_capital", "discount", "invested",
              "net_debt", "kd", "ke", "beta")
_COLD_FILES = ("identity_checks.py",)          # diagnostic audit, not fed to IV
_COLD_FUNC_HINTS = ("check_", "rollforward", "_audit", "roll_forward")


def _suggest(site: dict) -> tuple[str, str]:
    f, fn, src = site["file"], site["function"].lower(), site["source"].lower()
    if any(f.endswith(c) for c in _COLD_FILES) or any(h in fn for h in _COLD_FUNC_HINTS):
        return "COLD?", "diagnostic/audit path — not fed into valuation"
    blob = fn + " " + src
    hits = [t for t in _HOT_TERMS if t in blob]
    if hits:
        return "HOT?", "touches " + ",".join(sorted(set(hits))[:4])
    if "margin" in blob or "pct" in blob or "ratio" in blob:
        return "WARM?", "ratio/margin surface"
    return "WARM?", "review"


def write_markdown(sites: list[dict], path: Path) -> None:
    numeric = [s for s in sites if s["kind"] == "numeric_fallback"]
    chains = [s for s in sites if s["kind"] == "accessor_chain"]
    by_file: dict[str, int] = {}
    for s in numeric:
        by_file[s["file"]] = by_file.get(s["file"], 0) + 1

    lines = [
        "# Fallback substitution sites — Phase-0 enumeration (task 0.2.2)",
        "",
        "Complete inventory of falsy-fallback sites where a legitimate `0`/`None`",
        "can be replaced by a fabricated constant. **Gate: no Phase-1 code merges",
        "until every row's `criticality` is filled.**",
        "",
        "- **HOT** — feeds NOPAT / ROE / WACC / IV (a wrong value moves valuation)",
        "- **WARM** — feeds a ratio/margin shown to the user but not the IV",
        "- **COLD** — display-only / defensive / genuinely-safe default",
        "",
        f"**Totals:** {len(numeric)} numeric-constant fallbacks · "
        f"{len(chains)} `.get()` accessor chains · {len(sites)} sites total.",
        "",
        "### Numeric-constant fallbacks by file",
        "",
        "| file | count |",
        "|---|---|",
    ]
    for f, n in sorted(by_file.items(), key=lambda x: -x[1]):
        lines.append(f"| `{f}` | {n} |")
    lines += [
        "",
        "### All numeric-constant fallback sites",
        "",
        "`suggested` is a DRAFT heuristic; fill `confirmed` by hand (the gate).",
        "",
        "| # | file:line | function | const | suggested | confirmed | source |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(numeric, 1):
        src = s["source"].replace("|", "\\|")
        sug, _why = _suggest(s)
        lines.append(
            f"| {i} | `{s['file']}:{s['line']}` | `{s['function']}` | "
            f"`{s['const']}` | {sug} | _TODO_ | `{src}` |")
    lines += [
        "",
        "### `.get()` accessor chains (the falsy-zero accessor itself)",
        "",
        "| file:line | function | source |",
        "|---|---|---|",
    ]
    for s in chains:
        src = s["source"].replace("|", "\\|")
        lines.append(f"| `{s['file']}:{s['line']}` | `{s['function']}` | `{src}` |")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default=str(ROOT / "docs" / "fallback_sites.json"))
    ap.add_argument("--md-out", default=str(ROOT / "docs" / "fallback_sites.md"))
    args = ap.parse_args()

    sites = sweep()
    numeric = [s for s in sites if s["kind"] == "numeric_fallback"]
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_out, "w") as fh:
        json.dump(sites, fh, indent=2)
    write_markdown(sites, Path(args.md_out))

    by_file: dict[str, int] = {}
    for s in numeric:
        by_file[s["file"]] = by_file.get(s["file"], 0) + 1
    print(f"sites: {len(sites)} total · {len(numeric)} numeric-fallback · "
          f"{len(sites) - len(numeric)} accessor-chain")
    print("top files (numeric):")
    for f, n in sorted(by_file.items(), key=lambda x: -x[1])[:8]:
        print(f"  {n:3d}  {f}")
    print(f"wrote {args.json_out} + {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
