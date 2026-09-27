#!/usr/bin/env python3

"""
Additive schema: interview_preparations + interview_prep_questions --
Phase 3 (interview preparation).

interview_preparations: one row per (candidate, job) preparation
session -- persisted so it can be revisited, tied to which resume/
company-research version informed it. `outcome_status`/`outcome_notes`
capture post-interview feedback, kept separate from the generated
prep content itself.

interview_prep_questions: one row per question, normalized so a
candidate's own answer/confidence/notes can be filled in and updated
independently of the (deterministically generated, never regenerated
in place) question/model_answer pair. `model_answer_text` is either a
real excerpt/paraphrase of the candidate's own confirmed profile/resume
content, or the literal string "Preparation required -- no
demonstrated experience found." when no matching experience exists --
scripts/interview_prep.py must never fabricate an answer to fill this
column.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS interview_preparations (
            interview_prep_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            resume_id TEXT,
            company_research_id TEXT,
            status TEXT NOT NULL,
            overview_json TEXT,
            checklist_json TEXT,
            questions_to_ask_json TEXT,
            skill_gap_plan_json TEXT,
            outcome_status TEXT,
            outcome_notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            FOREIGN KEY(resume_id) REFERENCES resumes(resume_id),
            FOREIGN KEY(company_research_id) REFERENCES company_research(company_research_id),
            UNIQUE(candidate_id, job_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS interview_prep_questions (
            question_id TEXT PRIMARY KEY,
            interview_prep_id TEXT NOT NULL,
            category TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            model_answer_text TEXT NOT NULL,
            has_demonstrated_experience INTEGER NOT NULL,
            candidate_answer_text TEXT,
            confidence INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(interview_prep_id) REFERENCES interview_preparations(interview_prep_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interview_prep_questions_prep "
        "ON interview_prep_questions(interview_prep_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interview_preparations_candidate_job "
        "ON interview_preparations(candidate_id, job_id)"
    )


def migrate(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    _create_tables(conn)

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add interview_preparations + interview_prep_questions tables")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Tables ensured: interview_preparations, interview_prep_questions")


if __name__ == "__main__":
    main()
