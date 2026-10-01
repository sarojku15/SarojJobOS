#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

from experience_eligibility import assess_experience_eligibility, Eligibility


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Loaded here only so the CLI entry point (main(), below) can resolve
# the legacy single-candidate CLI pipeline's own local operator profile
# as the caller -- score_job() and evaluate_hard_reject() never read
# this global themselves; candidate_profile is always an explicit
# argument. config/profile.json is gitignored (an operator's own local
# file, never committed -- see config/profile.json.example), and this
# module is imported by the current multi-candidate pipeline too
# (scripts/search_worker.py etc.) purely for score_job()/
# evaluate_hard_reject(), which never touch this global -- so a fresh
# clone with no local config/profile.json yet must not fail to import
# this module. Falls back to the generic example so import always
# succeeds; the legacy CLI's own main() below still requires a real
# config/profile.json to actually run.
_PROFILE_PATH = ROOT / "config" / "profile.json"
if not _PROFILE_PATH.exists():
    _PROFILE_PATH = ROOT / "config" / "profile.json.example"
PROFILE = load_json(_PROFILE_PATH)


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def contains_any(text, keywords):
    text = normalize(text)
    return any(normalize(keyword) in text for keyword in keywords)


def matched_keywords(text, keywords):
    text = normalize(text)
    return [
        keyword
        for keyword in keywords
        if normalize(keyword) in text
    ]


# Generic industry acronym <-> full-phrase expansions -- domain
# vocabulary (like a dictionary), never candidate- or Saroj-specific
# data. Used only by role_alignment_score() below so "SRE" in one of
# candidate_profile["target_roles"] can still match a job titled
# "Site Reliability Engineer" (and vice versa) via word overlap,
# without falling back to a hardcoded, single-domain keyword list.
_ROLE_ACRONYM_EXPANSIONS = {
    "sre": "site reliability engineer",
    "devops": "development operations",
    "iac": "infrastructure as code",
    "cicd": "continuous integration continuous deployment",
    "ci/cd": "continuous integration continuous deployment",
    "sde": "software development engineer",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "qa": "quality assurance",
    "ux": "user experience",
    "ui": "user interface",
    "pm": "product manager",
    "ba": "business analyst",
    "hr": "human resources",
}

# Words that carry no discriminating signal for role-title alignment --
# seniority modifiers and near-universal tech-title nouns that would
# otherwise trivially "match" almost any two job titles.
_ROLE_ALIGNMENT_STOPWORDS = {
    "senior", "junior", "lead", "staff", "principal", "associate",
    "i", "ii", "iii", "iv", "the", "a", "an", "of", "and", "for",
    "engineer", "developer", "specialist", "manager", "architect",
}

_WORD_PATTERN = re.compile(r"[a-z0-9]+")


def _role_words(text):
    expanded = normalize(text)
    for acronym, expansion in _ROLE_ACRONYM_EXPANSIONS.items():
        if re.search(rf"\b{re.escape(acronym)}\b", expanded):
            expanded += " " + expansion
    return {w for w in _WORD_PATTERN.findall(expanded) if w not in _ROLE_ALIGNMENT_STOPWORDS}


def role_alignment_score(job_title, target_roles):
    """
    Whether `job_title` aligns with any of the candidate's OWN
    target_roles -- word-overlap based (with generic acronym
    expansion), not a hardcoded single-domain keyword list, so this
    genuinely reflects THIS candidate's targeted titles rather than
    always favoring one specific role family (see
    score_job()'s "Core role alignment" dimension).

    A candidate with no target_roles configured yet produces no match
    here (missing, not fabricated) -- exactly like every other
    unset/unknown candidate preference in this module.
    """
    if not target_roles:
        return False

    title_words = _role_words(job_title)
    if not title_words:
        return False

    for target_role in target_roles:
        target_words = _role_words(target_role)
        if not target_words:
            continue
        overlap = target_words & title_words
        # Every significant word of a short target role, or at least
        # half (rounded up) of a longer one -- forgiving enough for
        # real-world title variation ("Senior SRE" vs "Site
        # Reliability Engineer II"), strict enough that unrelated
        # titles don't spuriously match.
        required = max(1, -(-len(target_words) // 2))
        if len(overlap) >= required:
            return True
    return False


def evaluate_hard_reject(job, candidate_profile):
    title = normalize(job.get("title", ""))
    jd = normalize(job.get("jd_text", ""))

    reasons = []

    # THIS candidate's own excluded roles/titles (candidate_profile
    # ["excluded_roles"], from CandidateProfile.job_preferences.
    # excluded_roles via to_legacy_matching_profile()) -- never the
    # global SEARCHES.exclude_keywords, which was one specific
    # candidate's (Saroj's) own config/searches.json applied to EVERY
    # candidate regardless of who was actually being scored. Empty by
    # default, exactly like target_locations below -- never assumed.
    exclude_keywords = candidate_profile.get("excluded_roles", [])

    for keyword in exclude_keywords:
        if normalize(keyword) in title:
            reasons.append(f"Excluded title keyword: {keyword}")

    if contains_any(title, ["intern", "internship", "junior"]):
        reasons.append("Junior/intern-level role")

    mandatory_skills = job.get("mandatory_skills", [])

    profile_text = " ".join(
        [
            json.dumps(candidate_profile.get("cloud", [])),
            json.dumps(candidate_profile.get("kubernetes", [])),
            json.dumps(candidate_profile.get("iac_and_automation", [])),
            json.dumps(candidate_profile.get("cicd_and_devops", [])),
            json.dumps(candidate_profile.get("observability", [])),
            json.dumps(candidate_profile.get("sre", [])),
            json.dumps(candidate_profile.get("programming_and_scripting", [])),
            json.dumps(candidate_profile.get("tools", [])),
        ]
    )

    missing_mandatory = [
        skill
        for skill in mandatory_skills
        if normalize(skill) not in normalize(profile_text)
    ]

    if missing_mandatory:
        reasons.append(
            "Mandatory skill(s) not verified in profile: "
            + ", ".join(missing_mandatory)
        )

    return reasons


def score_job(job, candidate_profile, experience_assessment=None):
    """
    experience_assessment: an already-computed
    experience_eligibility.ExperienceAssessment, if the caller has one
    (e.g. prepare_jobs.py, which must run the eligibility gate before
    scoring anyway). When omitted, score_job() computes it itself by
    calling the same shared experience_eligibility.
    assess_experience_eligibility() -- never a second, independent
    experience parser -- so there is exactly one authoritative
    interpretation of a job's experience requirement, and the Experience
    dimension below can never contradict it.
    """
    title = normalize(job.get("title", ""))
    jd = normalize(job.get("jd_text", ""))
    location = normalize(job.get("location", ""))
    work_model = normalize(job.get("work_model", ""))

    combined = f"{title} {jd}"

    hard_reject_reasons = evaluate_hard_reject(job, candidate_profile)

    if experience_assessment is None:
        experience_assessment = assess_experience_eligibility(
            job, candidate_profile
        )

    score = 0
    matched = []
    missing = []

    # 1. Core role alignment — 20
    #
    # THIS candidate's own target_roles (candidate_profile
    # ["target_roles"], from CandidateProfile.job_preferences.
    # target_roles) -- never a single hardcoded, SRE/DevOps-specific
    # title-keyword list applied to every candidate regardless of what
    # role they're actually targeting. Word-overlap based (see
    # role_alignment_score()'s own docstring for why, and its generic
    # acronym handling for "SRE" vs "Site Reliability Engineer"-shaped
    # titles).
    target_roles = candidate_profile.get("target_roles", [])

    if role_alignment_score(title, target_roles):
        score += 20
        matched.append("Core role alignment")
    else:
        missing.append("Core role alignment")

    # 2. SRE / DevOps responsibilities — 15
    sre_keywords = [
        "slo",
        "sli",
        "sla",
        "error budget",
        "incident",
        "reliability",
        "observability",
        "production support",
        "root cause",
        "rca"
    ]

    sre_matches = matched_keywords(combined, sre_keywords)

    if len(sre_matches) >= 4:
        score += 15
        matched.append("SRE/DevOps responsibilities")
    elif len(sre_matches) >= 2:
        score += 10
        matched.append("Partial SRE/DevOps responsibilities")
    elif sre_matches:
        score += 5
        matched.append("Limited SRE/DevOps responsibilities")
    else:
        missing.append("SRE/DevOps responsibilities")

    # 3. AWS / Azure — 15
    # THIS candidate's own listed skills per category (candidate_profile
    # ["cloud"/"kubernetes"/"iac_and_automation"/"cicd_and_devops"/
    # ["observability"], from CandidateProfile.skills via
    # to_legacy_matching_profile()) -- never a single hardcoded,
    # Saroj-specific tool list applied to every candidate. A candidate
    # who lists no skills in a category naturally produces no match
    # there (missing, never fabricated) -- the same "unknown stays
    # unknown" behavior this module already uses for target_locations.
    cloud_matches = matched_keywords(combined, candidate_profile.get("cloud", []))

    if len(cloud_matches) >= 2:
        score += 15
        matched.append("Cloud alignment")
    elif cloud_matches:
        score += 10
        matched.append("Partial cloud alignment")
    else:
        missing.append("AWS/Azure")

    # 4. Kubernetes — 10
    k8s_matches = matched_keywords(combined, candidate_profile.get("kubernetes", []))

    if len(k8s_matches) >= 2:
        score += 10
        matched.append("Kubernetes")
    elif k8s_matches:
        score += 5
        matched.append("Partial Kubernetes")
    else:
        missing.append("Kubernetes")

    # 5. Terraform / IaC — 10
    iac_matches = matched_keywords(combined, candidate_profile.get("iac_and_automation", []))

    if len(iac_matches) >= 1:
        score += 10
        matched.append("Terraform/IaC")
    else:
        missing.append("Terraform/IaC")

    # 6. CI/CD — 10
    cicd_matches = matched_keywords(combined, candidate_profile.get("cicd_and_devops", []))

    if len(cicd_matches) >= 2:
        score += 10
        matched.append("CI/CD")
    elif cicd_matches:
        score += 5
        matched.append("Partial CI/CD")
    else:
        missing.append("CI/CD")

    # 7. Observability — 5
    observability_matches = matched_keywords(combined, candidate_profile.get("observability", []))

    if observability_matches:
        score += 5
        matched.append("Observability")
    else:
        missing.append("Observability")

    # 8. Experience — 5
    #
    # Sole authoritative source: experience_assessment, produced by
    # experience_eligibility.assess_experience_eligibility() -- the same
    # function the pre-scoring eligibility gate uses. This dimension
    # never re-parses experience_required itself, so it structurally
    # cannot disagree with the eligibility layer (e.g. flagging
    # "10-15 years" as exceeding an 11-year candidate's profile, when
    # eligibility already correctly says MATCH).
    if experience_assessment.eligibility == Eligibility.UNKNOWN:
        # Missing/unparseable data is not a penalty: give the benefit
        # of the doubt rather than inventing a mismatch from silence.
        score += 5
        matched.append("Experience requirement not specified")
    elif experience_assessment.eligibility in (
        Eligibility.MATCH,
        Eligibility.BORDERLINE,
    ):
        score += 5
        matched.append("Experience")
    else:
        # BELOW_PROFILE or ABOVE_PROFILE. No points awarded, but this is
        # informational, not a hard rejection -- evaluate_hard_reject()
        # above is the only mechanism that can force REJECT, and it is
        # untouched by this dimension. The reason text is taken
        # verbatim from the eligibility assessment rather than reworded
        # here, so the two layers can never say different things about
        # the same job.
        missing.append(experience_assessment.reason)

    # 9. Location / work model — 5
    target_locations = [
        normalize(x)
        for x in candidate_profile.get("target_locations", [])
    ]

    if any(loc in location for loc in target_locations):
        score += 5
        matched.append("Location")
    elif "remote" in work_model or "remote" in location:
        score += 5
        matched.append("Remote")
    else:
        missing.append("Location/work model")

    # 10. Overall/domain fit — 5
    domain_keywords = [
        "production",
        "cloud",
        "platform",
        "microservices",
        "infrastructure",
        "distributed systems",
        "reliability"
    ]

    domain_matches = matched_keywords(combined, domain_keywords)

    if len(domain_matches) >= 2:
        score += 5
        matched.append("Overall/domain fit")
    elif domain_matches:
        score += 3
        matched.append("Partial domain fit")
    else:
        missing.append("Overall/domain fit")

    if hard_reject_reasons:
        priority = "REJECT"
        # Phase 7.2 (data/reports/phase7_2_automation_status_audit.md):
        # "NOT_QUALIFIED" -- not "REJECTED" -- so this SCORE-BUCKET
        # outcome is never confusable with a future, genuine
        # application-lifecycle EMPLOYER_REJECTED status. Existing
        # production rows written before this change may still contain
        # the literal string "REJECTED" from this exact code path (see
        # that report for the backward-compatibility handling in
        # generate_run_report.py) -- this is a forward-only change,
        # not a migration.
        status = "NOT_QUALIFIED"
    elif score >= 90:
        priority = "A"
        status = "READY_FOR_APPROVAL"
    elif score >= 80:
        priority = "B"
        status = "READY_FOR_APPROVAL"
    elif score >= 70:
        priority = "C"
        status = "FOUND"
    else:
        priority = "REJECT"
        status = "NOT_QUALIFIED"

    return {
        "score": score,
        "priority": priority,
        "status": status,
        "matched_skills": matched,
        "missing_skills": missing,
        "hard_reject_reasons": hard_reject_reasons
    }


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/score_job.py <job.json>")
        sys.exit(1)

    job_path = Path(sys.argv[1])

    if not job_path.exists():
        print(f"Job file not found: {job_path}")
        sys.exit(1)

    job = load_json(job_path)

    # CLI caller resolves Saroj's profile explicitly; score_job() itself
    # has no Saroj-specific default.
    result = score_job(job, PROFILE)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
