#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "data" / "inbox" / "job_input.txt"
OUTPUT = ROOT / "data" / "inbox" / "parsed_job.json"


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def extract_source(text):
    match = re.search(r"(?im)^SOURCE:\s*(.+)$", text)
    return clean(match.group(1)).upper() if match else "MANUAL"


def extract_url(text):
    match = re.search(r"(?im)^URL:\s*(\S+)", text)
    if not match:
        raise ValueError("Missing URL: line")
    url = match.group(1).strip()

    # Remove accidental Markdown wrappers.
    url = url.strip("<>")
    url = re.sub(r"^\[", "", url)
    url = re.sub(r"\]\(.*$", "", url)

    return url


def extract_metadata(text, field):
    match = re.search(
        rf"(?im)^\s*{re.escape(field)}\s*:\s*(.+)$",
        text
    )
    return clean(match.group(1)) if match else ""


def clean_url(url):
    url = url.strip()

    # Extract the destination from Markdown:
    # [display text](https://actual-url)
    if "](" in url and ")" in url:
        destination = url.split("](", 1)[1].rsplit(")", 1)[0].strip()
        if destination.startswith(("http://", "https://")):
            return destination

    # Otherwise extract the first HTTP(S) URL present.
    import re
    match = re.search(r"https?://[^\\s)]+", url)
    if match:
        return match.group(0).rstrip("]")

    return url.strip("[]")

def extract_jd(text):
    marker = "PASTE THE COMPLETE JOB DESCRIPTION BELOW THIS LINE"

    if marker not in text:
        raise ValueError(
            "Missing JD marker: "
            "PASTE THE COMPLETE JOB DESCRIPTION BELOW THIS LINE"
        )

    jd = text.split(marker, 1)[1].strip()

    if not jd:
        raise ValueError("Job description is empty.")

    return jd

def extract_experience(jd):
    patterns = [
        r"\b\d+\s*(?:-|to)\s*\d+\s*years?\b",
        r"\b\d+\+?\s*years?\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, jd, re.IGNORECASE)
        if match:
            return clean(match.group(0))

    return ""


def extract_location(jd):
    patterns = [
        r"(?im)^\s*(?:location|locations)\s*[:\-]\s*(.+)$",
        r"(?im)^\s*(?:job location|work location)\s*[:\-]\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, jd)
        if match:
            return clean(match.group(1))

    return ""


def extract_work_model(jd):
    lower = jd.lower()

    if "fully remote" in lower or "100% remote" in lower:
        return "Remote"

    if re.search(r"\bremote\b", lower):
        return "Remote"

    if "hybrid" in lower:
        return "Hybrid"

    if any(x in lower for x in [
        "work from office",
        "work from the office",
        "onsite",
        "on-site",
        "office based",
    ]):
        return "WFO"

    return ""


def extract_company(jd):
    patterns = [
        r"(?im)^\s*(?:company|employer)\s*[:\-]\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, jd)
        if match:
            return clean(match.group(1))

    return ""


def extract_title(jd):
    patterns = [
        r"(?im)^\s*(?:job title|position|role)\s*[:\-]\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, jd)
        if match:
            return clean(match.group(1))

    return ""


def main():
    if not INPUT.exists():
        print(f"ERROR: Input not found: {INPUT}")
        sys.exit(1)

    text = INPUT.read_text(encoding="utf-8")

    source = extract_source(text)
    url = extract_url(text)
    jd = extract_jd(text)

    job = {
        "source": source,
        "job_id": "",
        "company": extract_metadata(text, "Company"),
        "title": extract_metadata(text, "Job Title"),
        "location": extract_metadata(text, "Location"),
        "work_model": extract_metadata(text, "Work Model"),
        "job_url": url,
        "application_url": url,
        "posted_date": extract_metadata(text, "Posted Date"),
        "experience_required": extract_metadata(text, "Experience"),
        "mandatory_skills": [],
        "preferred_skills": [],
        "jd_text": jd,
    }

    OUTPUT.write_text(
        json.dumps(job, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("PARSED JOB INPUT")
    print("================")
    print(f"Source      : {job['source']}")
    print(f"URL         : {job['job_url']}")
    print(f"Company     : {job['company'] or '[not detected]'}")
    print(f"Title       : {job['title'] or '[not detected]'}")
    print(f"Location    : {job['location'] or '[not detected]'}")
    print(f"Work model  : {job['work_model'] or '[not detected]'}")
    print(f"Experience  : {job['experience_required'] or '[not detected]'}")
    print(f"JD chars    : {len(jd)}")
    print()
    print(f"Output      : {OUTPUT}")


if __name__ == "__main__":
    main()
