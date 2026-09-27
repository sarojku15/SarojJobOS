#!/usr/bin/env python3
"""
Phase 3: interview preparation.

Deterministic, rule-based (same reasoning as scripts/resume_tailoring.py
-- no LLM call, no new dependency). Generates a real, persisted
interview-prep record from:
  - the candidate's own confirmed profile/resume (employment_history,
    skills)
  - the job's own scoring result (matched_skills/missing_skills, via
    the SAME score_job.py already used for matching -- never a second
    matching implementation)
  - the job's own company_research record, if one exists (never
    fabricated if none does)

Question bank (CATEGORY_QUESTIONS below) is a static, curated list of
genuinely common, generic industry interview questions per technology
category -- these are questions ABOUT THE FIELD, never claims about
the candidate, so there is no fabrication risk in the questions
themselves. The fabrication risk is entirely in the ANSWER, which this
module handles by construction:

  - If the candidate's own employment_history mentions the category's
    keywords, the "model answer" is a REAL, verbatim sentence from
    their own resume (never paraphrased/invented), explicitly labeled
    with which employer it came from.
  - If not, model_answer_text is the literal string "Preparation
    required -- no demonstrated experience found." and
    has_demonstrated_experience=False -- never invented.
  - Company-specific questions are only generated when a real
    company_research record with an actual description/recent_info
    snippet exists; otherwise that category is skipped entirely.
  - Resume-based questions are generated directly from the candidate's
    own real employment_history entries.
  - Behavioral/leadership questions get generic STAR-method guidance
    (how to structure an answer), never a fabricated specific answer,
    but do reference the candidate's own real employer names as where
    to draw an example from.
"""
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from candidate_profile import serialize_candidate_profile, to_legacy_matching_profile
from job_eligibility import assess_job_eligibility
from score_job import score_job, normalize


CATEGORY_QUESTIONS = {
    "kubernetes": {
        "keywords": ["kubernetes", "k8s", "eks", "aks", "gke", "helm"],
        "questions": [
            "How do you handle a pod that's stuck in CrashLoopBackOff?",
            "Explain the difference between a Deployment and a StatefulSet.",
            "How would you debug a service that's unreachable inside a cluster?",
        ],
    },
    "cloud": {
        "keywords": ["aws", "azure", "gcp", "cloud"],
        "questions": [
            "How do you design for high availability across multiple availability zones?",
            "Walk me through how you'd secure access to cloud resources for a team.",
        ],
    },
    "terraform_iac": {
        "keywords": ["terraform", "infrastructure as code", "ansible", "iac"],
        "questions": [
            "How do you manage Terraform state safely across a team?",
            "Describe a time you had to refactor infrastructure-as-code for reusability.",
        ],
    },
    "cicd": {
        "keywords": ["jenkins", "github actions", "gitlab ci", "argocd", "ci/cd", "cicd"],
        "questions": [
            "How would you design a CI/CD pipeline with safe rollback?",
            "What's your approach to zero-downtime deployments?",
        ],
    },
    "observability": {
        "keywords": ["prometheus", "grafana", "splunk", "dynatrace", "opentelemetry", "observability", "monitoring"],
        "questions": [
            "How do you decide what to alert on versus what to just log?",
            "Describe how you've reduced alert fatigue on a team.",
        ],
    },
    "incident_management": {
        "keywords": ["incident", "rca", "root cause", "postmortem", "post-incident", "mttr"],
        "questions": [
            "Walk me through how you ran a recent incident from detection to resolution.",
            "How do you run a blameless post-incident review?",
        ],
    },
    "sli_slo": {
        "keywords": ["sli", "slo", "sla", "error budget"],
        "questions": [
            "How do you define an SLO for a service you didn't build?",
            "What happens on your team when an error budget is exhausted?",
        ],
    },
}

BEHAVIORAL_QUESTIONS = [
    "Tell me about a time you disagreed with a technical decision. What did you do?",
    "Describe a project that failed. What did you learn?",
    "Tell me about a time you had to work under significant time pressure.",
]

LEADERSHIP_QUESTIONS = [
    "How do you mentor a junior engineer who is struggling?",
    "Describe how you've influenced a technical decision without direct authority.",
]

QUESTIONS_TO_ASK_INTERVIEWER = [
    "What does success look like in this role after the first 90 days?",
    "What's the biggest operational challenge the team is facing right now?",
    "How is on-call structured, and what's the current incident load like?",
    "What does the team's current tech stack look like, and are there any planned migrations?",
]

INTERVIEW_CHECKLIST = [
    "Review the job description and your tailored resume together.",
    "Re-read your own notes on this company's research (if available).",
    "Prepare 2-3 concrete examples from your own experience for behavioral questions.",
    "Prepare your own questions for the interviewer.",
    "Confirm interview logistics (time, timezone, format/link).",
]


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _mentions_any(text, keywords):
    normalized = normalize(text)
    return any(normalize(k) in normalized for k in keywords if k)


def _find_real_evidence(employment_history, keywords):
    """Returns (sentence, employer) for the first employment_history
    entry whose description genuinely mentions one of `keywords`, or
    (None, None) if there is no such evidence anywhere in the resume."""
    for entry in employment_history or []:
        entry_skills = " ".join(entry.get("skills") or [])
        text = f"{entry.get('description') or ''} {entry_skills}"
        if _mentions_any(text, keywords):
            sentences = re.split(r"(?<=[.!?])\s+", (entry.get("description") or "").strip())
            for s in sentences:
                if _mentions_any(s, keywords):
                    return s.strip(), entry.get("employer")
            return (entry.get("description") or "").strip(), entry.get("employer")
    return None, None


def build_interview_prep_content(candidate_profile_doc, job, matched_skills, missing_skills, company_research):
    employment_history = candidate_profile_doc.get("employment_history") or []
    employers = [e.get("employer") for e in employment_history if e.get("employer")]

    questions = []
    seq = 0
    for category, spec in CATEGORY_QUESTIONS.items():
        sentence, employer = _find_real_evidence(employment_history, spec["keywords"])
        has_evidence = sentence is not None
        for q in spec["questions"]:
            seq += 1
            if has_evidence:
                model_answer = f"Draw from your own experience: \"{sentence}\" (at {employer})."
            else:
                model_answer = "Preparation required -- no demonstrated experience found."
            questions.append({
                "category": category, "sequence": seq, "question": q,
                "model_answer": model_answer, "has_demonstrated_experience": has_evidence,
            })

    for q in BEHAVIORAL_QUESTIONS:
        seq += 1
        example_note = f" (draw a real example from: {', '.join(employers[:3])})" if employers else ""
        questions.append({
            "category": "behavioral", "sequence": seq, "question": q,
            "model_answer": f"Use the STAR method (Situation, Task, Action, Result){example_note}.",
            "has_demonstrated_experience": bool(employers),
        })

    for q in LEADERSHIP_QUESTIONS:
        seq += 1
        example_note = f" (draw a real example from: {', '.join(employers[:3])})" if employers else ""
        questions.append({
            "category": "leadership", "sequence": seq, "question": q,
            "model_answer": f"Use the STAR method (Situation, Task, Action, Result){example_note}.",
            "has_demonstrated_experience": bool(employers),
        })

    # Resume-based: directly from the candidate's own real history.
    for entry in employment_history:
        seq += 1
        questions.append({
            "category": "resume", "sequence": seq,
            "question": f"Tell me about your role as {entry.get('title')} at {entry.get('employer')}.",
            "model_answer": entry.get("description") or "Preparation required -- no demonstrated experience found.",
            "has_demonstrated_experience": bool(entry.get("description")),
        })

    # Company-specific: ONLY from a real company_research record with
    # real content -- never fabricated if none exists.
    if company_research and (company_research.get("description") or company_research.get("recent_info")):
        company = job.get("company") or "this company"
        if company_research.get("description"):
            seq += 1
            questions.append({
                "category": "company_specific", "sequence": seq,
                "question": f"What do you know about {company}?",
                "model_answer": f"{company_research['description']} (source: {company_research.get('website') or 'researched source'})",
                "has_demonstrated_experience": True,
            })
        for info in (company_research.get("recent_info") or [])[:2]:
            seq += 1
            questions.append({
                "category": "company_specific", "sequence": seq,
                "question": f"What recent information do you know about {company}?",
                "model_answer": f"{info.get('snippet')} (source: {info.get('url')})",
                "has_demonstrated_experience": True,
            })

    skill_gap_plan = [
        f"You don't have demonstrated experience with: {skill}. Review fundamentals and, if you have any tangential exposure, prepare an honest answer about your learning approach rather than overstating it."
        for skill in (missing_skills or [])
    ]

    overview = {
        "target_role": job.get("title"),
        "target_company": job.get("company"),
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "total_questions": len(questions),
    }

    return overview, questions, QUESTIONS_TO_ASK_INTERVIEWER, INTERVIEW_CHECKLIST, skill_gap_plan


def create_interview_prep(conn, candidate_id, job_id, candidate_profile, resume_id=None, company_research_id=None):
    """
    The one entry point. Raises ValueError on an unknown job. Never
    overwrites an existing prep for (candidate_id, job_id) silently --
    a second call regenerates the SAME row's questions (an explicit
    "regenerate," not a hidden duplicate), matching the UNIQUE
    (candidate_id, job_id) constraint in migrate_v13_interview_prep.py.
    """
    conn.row_factory = sqlite3.Row
    job_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if job_row is None:
        raise ValueError(f"Unknown job: {job_id!r}")
    job = dict(job_row)

    if resume_id is not None:
        # Same ownership check company_research_id already gets below --
        # never persist a resume_id belonging to a different candidate,
        # even though nothing downstream dereferences it today.
        owned = conn.execute(
            "SELECT 1 FROM resumes WHERE resume_id = ? AND candidate_id = ?",
            (resume_id, candidate_id),
        ).fetchone()
        if owned is None:
            raise ValueError(f"Resume {resume_id!r} does not belong to candidate {candidate_id!r}")

    candidate_profile_doc = serialize_candidate_profile(candidate_profile)
    legacy_profile = to_legacy_matching_profile(candidate_profile)

    # Same gate every other production caller of score_job() runs
    # first (see scripts/test_job_eligibility_integration.py's B5
    # check) -- never generate interview prep for a job this candidate
    # isn't even eligible for.
    eligibility_result = assess_job_eligibility(job, legacy_profile)
    if not eligibility_result.eligible:
        raise ValueError(
            f"Job {job_id!r} is not eligible for this candidate ({eligibility_result.reason}) -- "
            f"cannot generate interview preparation for it."
        )

    score_result = score_job(job, legacy_profile, experience_assessment=eligibility_result.experience_assessment)

    company_research = None
    if company_research_id:
        row = conn.execute(
            "SELECT * FROM company_research WHERE company_research_id = ? AND candidate_id = ?",
            (company_research_id, candidate_id),
        ).fetchone()
        if row:
            d = dict(row)
            d["recent_info"] = json.loads(d.get("recent_info_json") or "[]")
            company_research = d

    overview, questions, questions_to_ask, checklist, skill_gap_plan = build_interview_prep_content(
        candidate_profile_doc, job, score_result["matched_skills"], score_result["missing_skills"], company_research
    )

    existing = conn.execute(
        "SELECT interview_prep_id FROM interview_preparations WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, job_id),
    ).fetchone()

    now = _now()
    if existing:
        interview_prep_id = existing["interview_prep_id"]
        conn.execute(
            """
            UPDATE interview_preparations
            SET resume_id = ?, company_research_id = ?, status = 'READY',
                overview_json = ?, checklist_json = ?, questions_to_ask_json = ?,
                skill_gap_plan_json = ?, updated_at = ?
            WHERE interview_prep_id = ?
            """,
            (resume_id, company_research_id, json.dumps(overview), json.dumps(checklist),
             json.dumps(questions_to_ask), json.dumps(skill_gap_plan), now, interview_prep_id),
        )
        conn.execute("DELETE FROM interview_prep_questions WHERE interview_prep_id = ?", (interview_prep_id,))
    else:
        interview_prep_id = f"interview_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO interview_preparations (
                interview_prep_id, candidate_id, job_id, resume_id, company_research_id,
                status, overview_json, checklist_json, questions_to_ask_json,
                skill_gap_plan_json, outcome_status, outcome_notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'READY', ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (interview_prep_id, candidate_id, job_id, resume_id, company_research_id,
             json.dumps(overview), json.dumps(checklist), json.dumps(questions_to_ask),
             json.dumps(skill_gap_plan), now, now),
        )

    for q in questions:
        question_id = f"q_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO interview_prep_questions (
                question_id, interview_prep_id, category, sequence, question_text,
                model_answer_text, has_demonstrated_experience, candidate_answer_text,
                confidence, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (question_id, interview_prep_id, q["category"], q["sequence"], q["question"],
             q["model_answer"], 1 if q["has_demonstrated_experience"] else 0, now, now),
        )
    conn.commit()

    return get_interview_prep(conn, candidate_id, interview_prep_id)


def get_interview_prep(conn, candidate_id, interview_prep_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM interview_preparations WHERE interview_prep_id = ? AND candidate_id = ?",
        (interview_prep_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown interview prep: {interview_prep_id!r}")
    prep = dict(row)
    for field in ("overview_json", "checklist_json", "questions_to_ask_json", "skill_gap_plan_json"):
        key = field[: -len("_json")]
        prep[key] = json.loads(prep.pop(field) or "null")

    q_rows = conn.execute(
        "SELECT * FROM interview_prep_questions WHERE interview_prep_id = ? ORDER BY sequence",
        (interview_prep_id,),
    ).fetchall()
    prep["questions"] = [dict(q) for q in q_rows]
    return prep


def update_question_answer(conn, candidate_id, question_id, candidate_answer=None, confidence=None, notes=None):
    """Candidate fills in their OWN answer/confidence/notes for one
    question -- never touches question_text/model_answer_text (those
    are only ever regenerated by create_interview_prep(), never edited
    in place)."""
    row = conn.execute(
        """
        SELECT q.question_id FROM interview_prep_questions q
        JOIN interview_preparations p ON p.interview_prep_id = q.interview_prep_id
        WHERE q.question_id = ? AND p.candidate_id = ?
        """,
        (question_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown question {question_id!r} for this candidate")

    now = _now()
    conn.execute(
        "UPDATE interview_prep_questions SET candidate_answer_text = ?, confidence = ?, notes = ?, updated_at = ? WHERE question_id = ?",
        (candidate_answer, confidence, notes, now, question_id),
    )
    conn.commit()


def update_outcome(conn, candidate_id, interview_prep_id, outcome_status=None, outcome_notes=None):
    row = conn.execute(
        "SELECT interview_prep_id FROM interview_preparations WHERE interview_prep_id = ? AND candidate_id = ?",
        (interview_prep_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown interview prep {interview_prep_id!r} for this candidate")
    conn.execute(
        "UPDATE interview_preparations SET outcome_status = ?, outcome_notes = ?, updated_at = ? WHERE interview_prep_id = ?",
        (outcome_status, outcome_notes, _now(), interview_prep_id),
    )
    conn.commit()
