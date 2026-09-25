#!/usr/bin/env python3

import csv
import html
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "applications" / "jobos.db"
REPORT_DIR = BASE / "data" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now().strftime("%Y-%m-%d")
HTML_FILE = REPORT_DIR / f"daily_jobs_{TODAY}.html"
CSV_FILE = REPORT_DIR / f"daily_jobs_{TODAY}.csv"

# Only jobs that are still candidates for manual application.
ACTIVE_STATUSES = (
    "FOUND",
    "SCREENING",
    "SHORTLISTED",
    "READY_FOR_APPROVAL",
    "APPROVED",
)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

placeholders = ",".join("?" for _ in ACTIVE_STATUSES)

rows = conn.execute(
    f"""
    SELECT
        source,
        company,
        title,
        location,
        work_model,
        job_url,
        application_url,
        posted_date,
        score,
        priority,
        matched_skills,
        missing_skills,
        resume_variant,
        status,
        last_updated
    FROM jobs
    WHERE score >= 70
      AND source NOT IN ('TEST', 'MOCK')
      AND status IN ({placeholders})
    ORDER BY score DESC,
             CASE priority
                 WHEN 'A' THEN 1
                 WHEN 'B' THEN 2
                 WHEN 'C' THEN 3
                 ELSE 4
             END,
             COALESCE(posted_date, '') DESC
    """,
    ACTIVE_STATUSES,
).fetchall()

# Counts for the report header.
qualified_count = len(rows)

applied_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM jobs
    WHERE status IN (
        'APPLICATION_STARTED',
        'APPLIED',
        'INTERVIEW_1',
        'INTERVIEW_2',
        'FINAL_ROUND',
        'OFFER',
        'REJECTED',
        'GHOSTED',
        'WITHDRAWN'
    )
    """
).fetchone()[0]

a_count = sum(1 for r in rows if r["priority"] == "A")
b_count = sum(1 for r in rows if r["priority"] == "B")
c_count = sum(1 for r in rows if r["priority"] == "C")

# CSV
with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "Score",
        "Priority",
        "Company",
        "Title",
        "Location",
        "Work Model",
        "Source",
        "Posted",
        "Resume",
        "Status",
        "Matched Skills",
        "Missing Skills",
        "Apply URL",
        "Job URL",
    ])

    for r in rows:
        writer.writerow([
            r["score"],
            r["priority"],
            r["company"],
            r["title"],
            r["location"],
            r["work_model"],
            r["source"],
            r["posted_date"],
            r["resume_variant"],
            r["status"],
            r["matched_skills"],
            r["missing_skills"],
            r["application_url"] or r["job_url"],
            r["job_url"],
        ])

def esc(value):
    return html.escape(str(value or ""))

cards = []

for r in rows:
    apply_url = r["application_url"] or r["job_url"] or ""
    job_url = r["job_url"] or apply_url

    matched = [
        x.strip()
        for x in (r["matched_skills"] or "").split(",")
        if x.strip()
    ]

    missing = [
        x.strip()
        for x in (r["missing_skills"] or "").split(",")
        if x.strip()
    ]

    matched_html = "".join(
        f'<span class="tag matched">✓ {esc(x)}</span>' for x in matched
    )

    missing_html = "".join(
        f'<span class="tag missing">− {esc(x)}</span>' for x in missing
    )

    cards.append(f"""
    <div class="job">
      <div class="score score-{esc(r['priority'])}">
        {esc(r['score'])}
      </div>

      <div class="content">
        <div class="topline">
          <span class="priority">{esc(r['priority'])}</span>
          <span class="source">{esc(r['source'])}</span>
          <span class="status">{esc(r['status'])}</span>
        </div>

        <h2>{esc(r['title'])}</h2>

        <div class="company">
          {esc(r['company'])}
        </div>

        <div class="meta">
          <span>📍 {esc(r['location'])}</span>
          <span>🏢 {esc(r['work_model'])}</span>
          <span>📄 {esc(r['resume_variant'])}</span>
          <span>📅 {esc(r['posted_date'])}</span>
        </div>

        <div class="skills">
          {matched_html}
          {missing_html}
        </div>

        <div class="actions">
          <a class="apply" href="{esc(apply_url)}" target="_blank">
            OPEN APPLY PAGE →
          </a>
          <a class="joblink" href="{esc(job_url)}" target="_blank">
            Job Details
          </a>
        </div>
      </div>
    </div>
    """)

if not cards:
    cards_html = """
    <div class="empty">
      No active jobs with a score of 70 or higher are currently ready
      for manual application.
    </div>
    """
else:
    cards_html = "\n".join(cards)

generated = datetime.now().strftime("%d %b %Y %H:%M")

html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Saroj JobOS — Daily Job Report</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f7fa;
    margin: 0;
    padding: 32px;
    color: #172033;
}}

.container {{
    max-width: 1100px;
    margin: auto;
}}

h1 {{
    margin-bottom: 4px;
}}

.subtitle {{
    color: #667085;
    margin-bottom: 24px;
}}

.summary {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 24px;
}}

.metric {{
    background: white;
    border-radius: 12px;
    padding: 14px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
}}

.metric strong {{
    font-size: 22px;
    display: block;
}}

.job {{
    display: flex;
    background: white;
    border-radius: 14px;
    margin-bottom: 16px;
    padding: 20px;
    box-shadow: 0 1px 5px rgba(0,0,0,.08);
}}

.score {{
    width: 58px;
    height: 58px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    font-weight: 700;
    margin-right: 20px;
    background: #eef2f6;
}}

.score-A {{
    background: #dff7e8;
    color: #087443;
}}

.score-B {{
    background: #e7f0ff;
    color: #175cd3;
}}

.score-C {{
    background: #fff3d6;
    color: #b54708;
}}

.content {{
    flex: 1;
}}

.topline {{
    display: flex;
    gap: 8px;
    margin-bottom: 7px;
}}

.priority,
.source,
.status {{
    font-size: 12px;
    padding: 3px 8px;
    border-radius: 20px;
    background: #eef2f6;
}}

h2 {{
    margin: 4px 0;
    font-size: 19px;
}}

.company {{
    font-weight: 600;
    color: #475467;
}}

.meta {{
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin: 12px 0;
    color: #667085;
    font-size: 13px;
}}

.skills {{
    margin: 10px 0;
}}

.tag {{
    display: inline-block;
    padding: 4px 8px;
    margin: 3px;
    border-radius: 6px;
    font-size: 12px;
}}

.matched {{
    background: #ecfdf3;
    color: #067647;
}}

.missing {{
    background: #fef3f2;
    color: #b42318;
}}

.actions {{
    margin-top: 14px;
}}

.apply {{
    display: inline-block;
    background: #175cd3;
    color: white;
    padding: 9px 14px;
    border-radius: 7px;
    text-decoration: none;
    font-weight: 600;
    margin-right: 8px;
}}

.joblink {{
    color: #175cd3;
    text-decoration: none;
}}

.empty {{
    background: white;
    padding: 30px;
    border-radius: 12px;
    text-align: center;
}}
</style>
</head>

<body>
<div class="container">

<h1>JobOS — Daily Job Report</h1>
<div class="subtitle">
Generated {esc(generated)} · Manual application workflow
</div>

<div class="summary">
  <div class="metric">
    <strong>{qualified_count}</strong>
    Qualified ≥70
  </div>

  <div class="metric">
    <strong>{a_count}</strong>
    Priority A
  </div>

  <div class="metric">
    <strong>{b_count}</strong>
    Priority B
  </div>

  <div class="metric">
    <strong>{c_count}</strong>
    Priority C
  </div>

  <div class="metric">
    <strong>{applied_count}</strong>
    Existing lifecycle
  </div>
</div>

{cards_html}

</div>
</body>
</html>
"""

HTML_FILE.write_text(html_doc, encoding="utf-8")

conn.close()

print("DAILY JOB REPORT GENERATED")
print("==========================")
print(f"Qualified jobs >=70 : {qualified_count}")
print(f"Priority A          : {a_count}")
print(f"Priority B          : {b_count}")
print(f"Priority C          : {c_count}")
print(f"Existing lifecycle  : {applied_count}")
print()
print(f"HTML: {HTML_FILE}")
print(f"CSV : {CSV_FILE}")
