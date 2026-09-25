#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "inbox" / "jobs.json"


def ask(label, required=True):
    while True:
        value = input(f"{label}: ").strip()
        if value or not required:
            return value
        print("This field is required.")


def main():
    print()
    print("JobOS — Add Real Job")
    print("====================")
    print("Enter the job information from the job-board/company page.")
    print()

    source = ask("Source (NAUKRI/LINKEDIN/INDEED/FOUNDit/COMPANY/etc.)")
    company = ask("Company")
    title = ask("Job title")
    location = ask("Location")
    work_model = ask("Work model (Remote/Hybrid/WFO/Unknown)", required=False)
    job_url = ask("Job URL")
    application_url = ask(
        "Application URL (press Enter to use Job URL)",
        required=False
    )

    if not application_url:
        application_url = job_url

    posted_date = ask(
        "Posted date (optional)",
        required=False
    )

    experience_required = ask(
        "Experience required (optional)",
        required=False
    )

    print()
    print("Paste the COMPLETE JD text.")
    print("When finished, press Enter on an empty line.")
    print()

    jd_lines = []

    while True:
        line = input()
        if line == "":
            break
        jd_lines.append(line)

    jd_text = "\n".join(jd_lines).strip()

    if not jd_text:
        print("ERROR: JD text is required.")
        sys.exit(1)

    job = {
        "source": source.upper(),
        "job_id": "",
        "company": company,
        "title": title,
        "location": location,
        "work_model": work_model,
        "job_url": job_url,
        "application_url": application_url,
        "posted_date": posted_date,
        "experience_required": experience_required,
        "mandatory_skills": [],
        "preferred_skills": [],
        "jd_text": jd_text
    }

    existing = []

    if INBOX.exists():
        try:
            existing = json.loads(INBOX.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("ERROR: jobs.json contains invalid JSON.")
            sys.exit(1)

    if not isinstance(existing, list):
        print("ERROR: jobs.json must contain a JSON array.")
        sys.exit(1)

    existing.append(job)

    INBOX.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print()
    print("JOB ADDED TO INBOX")
    print("==================")
    print(f"Source      : {source.upper()}")
    print(f"Company     : {company}")
    print(f"Title       : {title}")
    print(f"Location    : {location}")
    print(f"Job URL     : {job_url}")
    print(f"Apply URL   : {application_url}")
    print(f"JD chars    : {len(jd_text)}")
    print()
    print(f"Inbox      : {INBOX}")
    print()
    print("Next command:")
    print("python3 scripts/ingest_jobs.py data/inbox/jobs.json")


if __name__ == "__main__":
    main()
