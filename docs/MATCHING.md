# Matching & Scoring

**Matching is deterministic and rule-based — not AI, not an LLM.**
Every point awarded comes from a fixed, readable rule comparing a
job's text/fields against your own confirmed profile. The same job and
the same profile always produce the same score; nothing is guessed or
generated.

The authoritative weight table lives in [SCORING.md](SCORING.md) —
this page explains the *concepts* behind it rather than duplicating
the numbers.

## The two-stage model

1. **Hard eligibility gate** (experience + location) and the
   **mandatory-skill gate** run *before* any points are awarded. If a
   job explicitly requires a skill your profile doesn't have, or your
   experience/location genuinely doesn't fit, the job is rejected
   outright — **a high numeric score can never override this gate.**
2. Only jobs that pass both gates get the **100-point score**, split
   across 10 dimensions (role alignment, SRE/DevOps responsibilities,
   cloud alignment, Kubernetes, Terraform/IaC, CI/CD, observability,
   experience, location, overall domain fit) — see
   [SCORING.md](SCORING.md) for the exact point value and rule behind
   each one.

## Why two candidates get different scores for the same job

6 of the 10 dimensions compare the job against **your own** profile
content — your `target_roles`, your listed cloud/Kubernetes/IaC/CI-CD/
observability skills. A different candidate with a different profile
gets a different score for the identical job, because the comparison
target (their profile) is different — there is no single "universal"
score for a job, only a score *for you*.

## Qualification threshold

A search's **minimum match score** field (set when you create/edit the
search) is the cutoff — jobs scoring below it are excluded from your
results entirely, not just hidden. Priority tiers (A/B/C/Reject) are
assigned from the score itself (see [SCORING.md](SCORING.md)'s
threshold table).

## Score explanation

Every result carries its own breakdown — click into any job for:
- the raw score and priority tier
- **Matched Skills** — exactly which dimensions scored points, and why
- **Gaps** — dimensions that scored zero or partial, and what was
  missing from your own profile to score fully

Nothing here is inferred beyond what's explicitly present or absent in
your listed skills — a gap is never guessed from context.

## Worked fictional example

**Alex Doe's profile**: target role "Senior Site Reliability
Engineer"; skills include Kubernetes, AWS, Azure, Terraform, Python,
Prometheus; 8 years experience; target locations Bengaluru, Remote.

**Job**: "Senior SRE — Bengaluru", requires Kubernetes + Terraform
(mandatory), mentions AWS, Prometheus, Grafana, incident response,
8+ years experience.

- Mandatory-skill gate: job requires Kubernetes + Terraform → Alex has
  both → **gate passes.**
- Core role alignment (20): "Senior Site Reliability Engineer" matches
  Alex's target role → full 20.
- SRE/DevOps responsibilities (15): "incident response" present → at
  least partial credit.
- Cloud alignment: AWS mentioned, Alex lists AWS → credit.
- Kubernetes (10): Alex lists it → full credit.
- Terraform/IaC (10): Alex lists it → full credit.
- Observability (5): Prometheus mentioned, Alex lists it → full credit.
- Location (5): Bengaluru matches Alex's target locations → full
  credit.

Result: a high score, likely priority A or B depending on the exact
point total — see [SCORING.md](SCORING.md) for how the final number
maps to a tier. If the job instead required a skill *missing* from
Alex's profile as mandatory (say, "GCP" with no AWS/Azure substitute
accepted), the mandatory gate would reject it regardless of how well
everything else matched.
