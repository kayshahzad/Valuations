#!/usr/bin/env python3
# scratch/audit_tolerance_budget.py

import os
import re
from pathlib import Path

def audit_tolerances(test_dir="tests/calculation_layer"):
    """
    Scans the test directory for any usage of floating point tolerances
    looser than the standard 1e-9 threshold.
    """
    print("=" * 60)
    print("TOLERANCE BUDGET AUDIT REPORT")
    print("=" * 60)
    
    # regex to find rel_tol=... or abs(...) < ...
    # Look for math.isclose with rel_tol > 1e-9
    isclose_pattern = re.compile(r'math\.isclose\([^)]*rel_tol=([^)]+)\)')
    abs_pattern = re.compile(r'abs\([^)]+\)\s*<\s*([0-9.]+)')
    
    found_issues = 0
    
    for root, _, files in os.walk(test_dir):
        for file in files:
            if not file.endswith(".py"):
                continue
                
            path = Path(root) / file
            with open(path, "r") as f:
                content = f.read()
                
            for i, line in enumerate(content.split("\n")):
                line_num = i + 1
                
                # Check isclose
                for match in isclose_pattern.finditer(line):
                    tol_str = match.group(1).strip()
                    try:
                        tol = float(tol_str)
                        if tol > 1e-9:
                            print(f"[LOOSE TOLERANCE] {path.name}:{line_num}")
                            print(f"  Tolerance: {tol}")
                            print(f"  Line: {line.strip()}")
                            found_issues += 1
                    except ValueError:
                        pass
                
                # Check explicit abs() < val
                for match in abs_pattern.finditer(line):
                    tol_str = match.group(1).strip()
                    try:
                        tol = float(tol_str)
                        # if it's less than 0.05 etc, it's a loose tolerance
                        if tol > 1e-9:
                            print(f"[EXPLICIT DELTA] {path.name}:{line_num}")
                            print(f"  Delta: {tol}")
                            print(f"  Line: {line.strip()}")
                            found_issues += 1
                    except ValueError:
                        pass
                        
    if found_issues == 0:
        print("No loose tolerances found! All tests adhere to 1e-9 strict limit.")
    else:
        print(f"\nFound {found_issues} instances of loose tolerances.")
        print("Please review and document reasons if legitimate, or tighten to 1e-9.")

if __name__ == "__main__":
    audit_tolerances()
