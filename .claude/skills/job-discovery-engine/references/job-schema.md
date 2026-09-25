# Schemas

## SearchCriteria (input to this Skill)

Generic; never assumes any profession. Matches
`api/schemas.SavedSearchCreate` field-for-field (this Skill and the API
layer share one shape, not two):

```json
{
  "titles": ["Senior Cloud Engineer"],
  "locations": ["Bangalore", "Hyderabad"],
  "experience_min": 8,
  "experience_max": 12,
  "salary_min": 2500000,
  "salary_max": null,
  "currency": "INR",
  "skills": ["AWS", "Terraform", "Kubernetes"],
  "work_model": ["REMOTE", "HYBRID"],
  "employment_type": ["FULL_TIME"],
  "max_job_age_days": 3,
  "minimum_match_score": 70,
  "sources": ["NAUKRI"]
}
```

## CommonJob (output shape -- the EXISTING raw-job dict every adapter
already returns; not a new schema)

```json
{
  "source": "NAUKRI",
  "company": "string",
  "title": "string",
  "location": "string",
  "work_model": "string",
  "job_url": "string (required for an actionable job)",
  "application_url": "string",
  "posted_date": "string or empty",
  "jd_text": "string",
  "experience_required": "string",
  "mandatory_skills": ["string", "..."],
  "preferred_skills": ["string", "..."]
}
```

This is exactly `source_adapter.MockJobSourceAdapter.search()`'s return
shape and `scripts/discover_local.normalize_job()`'s input shape --
every discovery mechanism in this project (adapter, ATS provider, web
search) must produce this shape, and only this shape, before jobs enter
`scripts/discover_local.py` / `search_worker.py`'s existing
normalization step.

## DiscoveryOutcome (this Skill's own return value)

```json
{
  "jobs": [ /* list of CommonJob dicts, already de-duplicated
               WITHIN this discovery call (not cross-source --
               that remains cross_source_dedup.py's job) */ ],
  "queries_run": [ {"source": "...", "role": "...", "location": "..."} ],
  "sources_used": ["NAUKRI"],
  "unavailable_sources": [ {"source_name": "HIRIST", "reason": "..."} ],
  "errors": [ {"source": "...", "detail": "..."} ]
}
```
