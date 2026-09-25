# NON-PRODUCTION VALIDATION ARTIFACTS

Everything in this directory was generated against a **temporary,
disposable SQLite database** created solely for Phase 7's SIXTH-step
live Naukri validation (`data/reports/phase7_reporting_audit.md`). It
is **not** part of the production system of record.

- `NON_PRODUCTION_naukri_tempdb_report.xlsx` — the 9-sheet workbook
  generated from that temp DB's data (1 live Naukri query: "Infrastructure
  Engineer" / "Bangalore", `max_job_age_days=3`).
- `NON_PRODUCTION_naukri_tempdb_validation_dump.json` — the full
  captured metrics from that run.

**`data/applications/jobos.db` (the real production DB) was never
opened or modified to produce anything in this directory.** See
`data/reports/phase7_reporting_audit.md`'s SIXTH-step section for the
full evidence trail (production DB SHA-256 before/after, row counts
before/after).
