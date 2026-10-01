#!/usr/bin/env python3

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


TESTS = [
    {
        "file": "data/fixtures/scoring/test_job.json",
        "expected_priority": "A",
        "expected_status": "READY_FOR_APPROVAL"
    },
    {
        "file": "data/fixtures/scoring/test_job_b.json",
        "expected_priority": "B",
        "expected_status": "READY_FOR_APPROVAL"
    },
    {
        "file": "data/fixtures/scoring/test_job_reject.json",
        "expected_priority": "REJECT",
        "expected_status": "NOT_QUALIFIED"  # Phase 7.2: score_job.py no longer writes "REJECTED" (see data/reports/phase7_2_automation_status_audit.md)
    }
]


def main():
    failures = []

    for test in TESTS:
        path = ROOT / test["file"]

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "score_job.py"),
                str(path)
            ],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            failures.append(
                f"{test['file']}: scoring engine failed\n{result.stderr}"
            )
            continue

        output = json.loads(result.stdout)

        if output["priority"] != test["expected_priority"]:
            failures.append(
                f"{test['file']}: expected priority "
                f"{test['expected_priority']}, got {output['priority']}"
            )

        if output["status"] != test["expected_status"]:
            failures.append(
                f"{test['file']}: expected status "
                f"{test['expected_status']}, got {output['status']}"
            )

        if not failures or not any(test["file"] in f for f in failures):
            print(
                f"PASS: {test['file']} -> "
                f"{output['score']} / {output['priority']} / {output['status']}"
            )

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(failure)
        sys.exit(1)

    print("\nAll scoring tests passed.")


if __name__ == "__main__":
    main()
