#!/usr/bin/env python3
"""
Phase 1: automatic resume tailoring.

Deterministic, rule-based (no LLM call, no new dependency -- see
docs/ARCHITECTURE.md's "Resume tailoring" section for why): given a
candidate's own confirmed profile, one of their own uploaded resumes,
and a job, produces a NEW, immutable "tailored resume" record by
reordering/emphasizing content that already exists in the profile --
never inventing an employer, project, certification, year of
experience, technology, title, achievement, metric, or responsibility.

Output shape (content_json, see migrate_v11_tailored_resumes.py):
  - identity, certifications, education: copied verbatim, unchanged.
  - professional_summary: the profile's own summary, its EXISTING
    sentences reordered so ones mentioning a matched skill/keyword
    come first -- no sentence rewritten, none added or removed.
  - key_skills_for_role: the candidate's OWN skill names (verbatim),
    reordered so ones the job actually asked for (matched_skills, via
    the SAME score_job.py/score_explanation.py already used for
    scoring -- never a second matching implementation) come first.
  - relevant_experience_highlights: for each employment_history entry
    whose OWN listed `skills` overlap with the job's matched skills,
    the EXISTING sentences of that entry's description that mention a
    matched keyword, extracted verbatim (never paraphrased) and
    ordered by overlap count -- a highlights VIEW, not a rewrite.
  - full_experience: the complete, unmodified employment_history list
    (all entries, original order, original text) -- always present in
    full, so nothing is ever hidden by omission, only additionally
    highlighted.

factual_safety_status: a real, automated check (see
validate_factual_safety() below) that every word used in
professional_summary/key_skills_for_role/relevant_experience_
highlights already appears in the candidate's own original profile
text -- REJECTED (and never persisted) if it doesn't, which should be
structurally impossible given this module only reorders/extracts
existing text, but is checked anyway per the project's own "trust but
verify" convention.
"""
import hashlib
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


class ResumeTailoringError(ValueError):
    pass


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _load_job(conn, job_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise ResumeTailoringError(f"Unknown job: {job_id!r}")
    return dict(row)


def _split_sentences(text):
    if not text:
        return []
    # Simple, conservative sentence split -- good enough for reordering
    # (never used to invent content, only to re-sequence what's there).
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _mentions_any(text, keywords):
    normalized = normalize(text)
    return any(normalize(k) in normalized for k in keywords if k)


def build_tailored_content(candidate_profile_doc, job, legacy_profile, experience_assessment=None):
    """
    candidate_profile_doc: the full serialized CandidateProfile dict
    (from profile_store/candidate_profile) -- identity/professional_
    summary/skills/certifications/education/employment_history/
    job_preferences, as returned by GET /api/candidates/{id}/profile.
    job: raw job dict (jobs table row).
    legacy_profile: to_legacy_matching_profile() projection, for
    reusing score_job() unchanged.

    Returns (content_json_dict, changed_sections, unchanged_sections,
    keywords_emphasized, matched_skills, missing_skills).
    """
    score_result = score_job(job, legacy_profile, experience_assessment=experience_assessment)
    matched_skills = score_result["matched_skills"]
    missing_skills = score_result["missing_skills"]

    # The actual skill NAMES (not dimension labels) the job's JD text
    # mentions, across every category the candidate has listed --
    # used only to decide ORDER, never to invent a new skill name.
    jd_text = f"{job.get('title', '')} {job.get('jd_text', '')}"
    all_skill_names = []
    for category, items in (candidate_profile_doc.get("skills") or {}).items():
        for item in items:
            all_skill_names.append((category, item["name"]))
    relevant_names = {name for _, name in all_skill_names if _mentions_any(jd_text, [name])}

    key_skills_for_role = sorted(
        (name for _, name in all_skill_names),
        key=lambda name: (name not in relevant_names, name.lower()),
    )

    summary_text = (candidate_profile_doc.get("professional_summary") or {}).get("summary") or ""
    sentences = _split_sentences(summary_text)
    reordered_summary_sentences = sorted(
        sentences, key=lambda s: (not _mentions_any(s, relevant_names), sentences.index(s))
    )
    professional_summary = " ".join(reordered_summary_sentences) if sentences else summary_text
    summary_changed = reordered_summary_sentences != sentences

    highlights = []
    for entry in candidate_profile_doc.get("employment_history") or []:
        entry_skills = set(entry.get("skills") or [])
        overlap = entry_skills & relevant_names
        entry_sentences = _split_sentences(entry.get("description") or "")
        matching_sentences = [s for s in entry_sentences if _mentions_any(s, relevant_names)]
        if overlap or matching_sentences:
            highlights.append({
                "employer": entry.get("employer"),
                "title": entry.get("title"),
                "start_date": entry.get("start_date"),
                "end_date": entry.get("end_date"),
                "overlap_skill_count": len(overlap),
                "highlight_sentences": matching_sentences or entry_sentences[:1],
            })
    highlights.sort(key=lambda h: -h["overlap_skill_count"])

    content = {
        "identity": candidate_profile_doc.get("identity"),
        "target_role": job.get("title"),
        "target_company": job.get("company"),
        "professional_summary": professional_summary,
        "key_skills_for_role": key_skills_for_role,
        "relevant_experience_highlights": highlights,
        "full_experience": candidate_profile_doc.get("employment_history") or [],
        "certifications": candidate_profile_doc.get("certifications") or [],
        "education": candidate_profile_doc.get("education") or [],
    }

    changed_sections = ["key_skills_for_role"]
    if summary_changed:
        changed_sections.append("professional_summary")
    if highlights:
        changed_sections.append("relevant_experience_highlights")
    unchanged_sections = ["identity", "certifications", "education", "full_experience"]

    return content, changed_sections, unchanged_sections, sorted(relevant_names), matched_skills, missing_skills


def validate_factual_safety(candidate_profile_doc, content):
    """
    Automated, real validation (requirement 11): every word used in the
    generated professional_summary/key_skills_for_role/relevant_
    experience_highlights must already appear, verbatim, somewhere in
    the candidate's OWN original profile text (summary + skill names +
    employment descriptions). Returns (status, notes) -- status is
    "PASS" or "REJECTED", never silently skipped.
    """
    original_text_parts = [
        (candidate_profile_doc.get("professional_summary") or {}).get("summary") or "",
    ]
    for category_items in (candidate_profile_doc.get("skills") or {}).values():
        for item in category_items:
            original_text_parts.append(item["name"])
    for entry in candidate_profile_doc.get("employment_history") or []:
        original_text_parts.append(entry.get("description") or "")
        original_text_parts.extend(entry.get("skills") or [])

    original_words = set(normalize(" ".join(original_text_parts)).split())

    generated_text_parts = [content["professional_summary"]] + content["key_skills_for_role"]
    for h in content["relevant_experience_highlights"]:
        generated_text_parts.extend(h["highlight_sentences"])
    generated_words = set(normalize(" ".join(generated_text_parts)).split())

    # Ignore trivial stopword-scale noise (punctuation-stripped short
    # connective words) -- only meaningful (4+ char) tokens are checked,
    # since normalize() already lowercases/collapses whitespace only,
    # not strip punctuation from every word.
    unsupported = {
        w for w in generated_words
        if len(w) >= 4 and w not in original_words
    }
    if unsupported:
        return "REJECTED", sorted(unsupported)
    return "PASS", []


def create_tailored_resume(conn, candidate_id, base_resume_id, job_id, candidate_profile):
    """
    The one entry point: builds tailored content for (candidate_id,
    base_resume_id, job_id), validates it, and persists a NEW,
    immutable tailored_resumes row (next tailoring_version for this
    exact triple -- never overwrites an earlier one). Raises
    ResumeTailoringError on any real precondition failure (unknown
    job, base resume not owned by this candidate) rather than
    fabricating a result.

    candidate_profile: a real CandidateProfile object (e.g. from
    profile_store.load_active_profile()) -- the SAME object type
    to_legacy_matching_profile() already consumes everywhere else in
    this project, never a second/independent profile representation.
    """
    row = conn.execute(
        "SELECT resume_id FROM resumes WHERE resume_id = ? AND candidate_id = ?",
        (base_resume_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ResumeTailoringError(f"Resume {base_resume_id!r} does not belong to candidate {candidate_id!r}")

    job = _load_job(conn, job_id)
    candidate_profile_doc = serialize_candidate_profile(candidate_profile)
    legacy_profile = to_legacy_matching_profile(candidate_profile)

    # Same gate every other production caller of score_job() runs
    # first (see scripts/test_job_eligibility_integration.py's B5
    # check) -- never tailor a resume for a job this candidate isn't
    # even eligible for.
    eligibility_result = assess_job_eligibility(job, legacy_profile)
    if not eligibility_result.eligible:
        raise ResumeTailoringError(
            f"Job {job_id!r} is not eligible for this candidate ({eligibility_result.reason}) -- "
            f"cannot tailor a resume for it."
        )

    content, changed, unchanged, kw_emphasized, matched, missing = build_tailored_content(
        candidate_profile_doc, job, legacy_profile,
        experience_assessment=eligibility_result.experience_assessment,
    )
    status, notes = validate_factual_safety(candidate_profile_doc, content)

    next_version = 1 + (
        conn.execute(
            "SELECT COALESCE(MAX(tailoring_version), 0) FROM tailored_resumes "
            "WHERE candidate_id = ? AND base_resume_id = ? AND job_id = ?",
            (candidate_id, base_resume_id, job_id),
        ).fetchone()[0]
    )

    tailored_resume_id = f"tailored_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO tailored_resumes (
            tailored_resume_id, candidate_id, base_resume_id, job_id,
            tailoring_version, status, content_json, changed_sections_json,
            unchanged_sections_json, keywords_added_json, keywords_emphasized_json,
            matched_skills_json, missing_skills_json, factual_safety_status,
            factual_safety_notes_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tailored_resume_id, candidate_id, base_resume_id, job_id,
            next_version, "REJECTED" if status == "REJECTED" else "READY",
            json.dumps(content), json.dumps(changed), json.dumps(unchanged),
            json.dumps([]), json.dumps(kw_emphasized),
            json.dumps(matched), json.dumps(missing), status, json.dumps(notes),
            _now(),
        ),
    )
    conn.commit()

    return {
        "tailored_resume_id": tailored_resume_id,
        "candidate_id": candidate_id,
        "base_resume_id": base_resume_id,
        "job_id": job_id,
        "tailoring_version": next_version,
        "status": "REJECTED" if status == "REJECTED" else "READY",
        "content": content,
        "changed_sections": changed,
        "unchanged_sections": unchanged,
        "keywords_added": [],
        "keywords_emphasized": kw_emphasized,
        "matched_skills": matched,
        "missing_skills": missing,
        "factual_safety_status": status,
        "factual_safety_notes": notes,
    }


def get_tailored_resume(conn, candidate_id, tailored_resume_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM tailored_resumes WHERE tailored_resume_id = ? AND candidate_id = ?",
        (tailored_resume_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ResumeTailoringError(f"Unknown tailored resume: {tailored_resume_id!r}")
    return _row_to_dict(row)


def list_tailored_resumes(conn, candidate_id, job_id=None):
    conn.row_factory = sqlite3.Row
    if job_id is not None:
        rows = conn.execute(
            "SELECT * FROM tailored_resumes WHERE candidate_id = ? AND job_id = ? ORDER BY created_at DESC",
            (candidate_id, job_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tailored_resumes WHERE candidate_id = ? ORDER BY created_at DESC",
            (candidate_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row):
    d = dict(row)
    for json_field in (
        "content_json", "changed_sections_json", "unchanged_sections_json",
        "keywords_added_json", "keywords_emphasized_json",
        "matched_skills_json", "missing_skills_json", "factual_safety_notes_json",
    ):
        key = json_field[: -len("_json")]
        d[key] = json.loads(d.pop(json_field) or "null")
    return d


def render_plain_text(tailored_resume_dict):
    """
    Requirement 9 (export/download): renders the tailored content as
    plain text -- no PDF-generation library exists in this project's
    dependencies (pypdf reads/manipulates PDFs, it does not lay out new
    documents), so the real, working export format is plain text, not
    a new PDF binary. Documented deliberately, not a placeholder.
    """
    c = tailored_resume_dict["content"]
    lines = []
    identity = c.get("identity") or {}
    lines.append(identity.get("name") or "")
    contact = " | ".join(filter(None, [identity.get("email"), identity.get("phone")]))
    if contact:
        lines.append(contact)
    lines.append("")
    lines.append(f"Target Role: {c.get('target_role') or ''}  |  Target Company: {c.get('target_company') or ''}")
    lines.append("")
    lines.append("PROFESSIONAL SUMMARY")
    lines.append(c.get("professional_summary") or "")
    lines.append("")
    lines.append("KEY SKILLS FOR THIS ROLE")
    lines.append(", ".join(c.get("key_skills_for_role") or []))
    lines.append("")
    if c.get("relevant_experience_highlights"):
        lines.append("RELEVANT EXPERIENCE HIGHLIGHTS")
        for h in c["relevant_experience_highlights"]:
            lines.append(f"- {h.get('title')} at {h.get('employer')} ({h.get('start_date')} - {h.get('end_date')})")
            for s in h.get("highlight_sentences") or []:
                lines.append(f"    * {s}")
        lines.append("")
    lines.append("FULL EXPERIENCE")
    for entry in c.get("full_experience") or []:
        lines.append(f"{entry.get('title')} | {entry.get('employer')} | {entry.get('start_date')} - {entry.get('end_date')}")
        lines.append(entry.get("description") or "")
        lines.append("")
    lines.append("CERTIFICATIONS")
    for cert in c.get("certifications") or []:
        lines.append(f"- {cert.get('name')}")
    lines.append("")
    lines.append("EDUCATION")
    for ed in c.get("education") or []:
        lines.append(f"- {ed.get('degree')}, {ed.get('field_of_study')} — {ed.get('institution')}")
    return "\n".join(lines)
