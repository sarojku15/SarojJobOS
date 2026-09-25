#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INPUT = ROOT / "data" / "inbox" / "job_input.txt"
PARSED = ROOT / "data" / "inbox" / "parsed_job.json"


def run(command):
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
    )

    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    if not INPUT.exists():
        print(f"ERROR: Input file not found: {INPUT}")
        sys.exit(1)

    print("JOBOS — PROCESS JOB")
    print("===================")
    print()

    print("[1/3] Parsing job input...")
    result = subprocess.run(
        ["python3", "scripts/parse_job_input.py"],
        cwd=ROOT,
        text=True,
    )

    if result.returncode != 0:
        print()
        print("Input was not processed.")
        print("Add a real job to data/inbox/job_input.txt and run again.")
        sys.exit(result.returncode)

    print()
    print("[2/3] Scoring and tracking job...")
    run([
        "python3",
        "scripts/ingest_job.py",
        "data/inbox/parsed_job.json",
    ])

    print()
    print("[3/3] Updating daily report...")
    run([
        "python3",
        "scripts/generate_daily_report.py",
    ])

    print()
    print("JOBOS PROCESS COMPLETE")


if __name__ == "__main__":
    main()
