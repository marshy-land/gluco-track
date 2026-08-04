#!/usr/bin/env python3
"""
Gluco Track E2E Test Suite Runner
Runs Tiers 1-4 E2E tests, prints formatted tier-by-tier progress, and returns exit code 0 on success.
"""

import os
import sys
import time
import unittest
from typing import Tuple, List, Dict, Any

# Configure stdout encoding to handle unicode safely if supported
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def run_test_tier(tier_name: str, module_name: str) -> Tuple[bool, int, int, float]:
    """Runs a single test tier module using unittest and prints formatted output."""
    print(f"\n======================================================================")
    print(f" Running {tier_name} ({module_name})")
    print(f"======================================================================")

    start_time = time.time()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(module_name)
    except Exception as e:
        print(f"[FAIL] Failed to load test module {module_name}: {e}")
        return False, 0, 1, time.time() - start_time

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed = time.time() - start_time

    passed = result.testsRun - len(result.failures) - len(result.errors)
    failed = len(result.failures) + len(result.errors)
    success = (failed == 0)

    status_str = "[PASS]" if success else "[FAIL]"
    print(f"\n--- {tier_name} Summary ---")
    print(f"Status:   {status_str}")
    print(f"Passed:   {passed}/{result.testsRun}")
    print(f"Failed:   {failed}")
    print(f"Duration: {elapsed:.3f} seconds\n")

    return success, passed, result.testsRun, elapsed


def main():
    print("======================================================================")
    print(" GLUCO TRACK E2E TEST SUITE RUNNER (M0)")
    print(" Requirements: R1 (Dietary Analysis), R2 (Imputation), R3 (Nutritional Impact)")
    print("======================================================================")

    tiers = [
        ("Tier 1: Feature Coverage", "e2e_tests.test_tier1_features"),
        ("Tier 2: Boundary & Corner Cases", "e2e_tests.test_tier2_boundaries"),
        ("Tier 3: Cross-Feature Interactions", "e2e_tests.test_tier3_interactions"),
        ("Tier 4: Real-World Scenarios", "e2e_tests.test_tier4_scenarios"),
    ]

    total_passed = 0
    total_run = 0
    total_time = 0.0
    overall_success = True
    tier_results = []

    for tier_name, module_name in tiers:
        success, passed, run_cnt, elapsed = run_test_tier(tier_name, module_name)
        tier_results.append((tier_name, success, passed, run_cnt, elapsed))
        total_passed += passed
        total_run += run_cnt
        total_time += elapsed
        if not success:
            overall_success = False

    print("======================================================================")
    print(" FINAL E2E TEST SUITE SUMMARY")
    print("======================================================================")
    for t_name, succ, p_cnt, r_cnt, dur in tier_results:
        st = "PASS" if succ else "FAIL"
        print(f"  [{st:<4}] {t_name:<35} {p_cnt}/{r_cnt} tests ({dur:.3f}s)")
    print("----------------------------------------------------------------------")
    print(f" Total Tests Run: {total_run}")
    print(f" Total Passed:    {total_passed}")
    print(f" Total Failed:    {total_run - total_passed}")
    print(f" Total Duration:  {total_time:.3f} seconds")
    print("======================================================================")

    if overall_success and total_run >= 36:
        print("[SUCCESS] ALL E2E TEST TIERS COMPLETED SUCCESSFULLY WITH 100% PASS RATE!")
        sys.exit(0)
    elif overall_success:
        print(f"[WARNING] ALL RUN TESTS PASSED, BUT TOTAL TESTS ({total_run}) IS LESS THAN REQUIRED 36.")
        sys.exit(0)
    else:
        print("[FAILURE] TEST SUITE FAILED — FIX ERRORS BEFORE PROCEEDING.")
        sys.exit(1)

if __name__ == "__main__":
    main()
