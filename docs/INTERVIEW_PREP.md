# Interview Preparation

Three genuinely separate concepts in this codebase — JobOS never
merges them:

| Concept | What it is |
|---|---|
| **Interview preparation** | A generated set of questions + suggested answers for one job, created on demand. |
| **Interview questions** | The individual question records inside a preparation, each with its own category and your own saved answer/confidence/notes. |
| **Interview outcome** | A single real fact you record afterward — what actually happened. |

**Application-question assistance** (help answering questions on an
employer's own application form) **is not implemented.**

## Opening it

From a job's workspace page, optionally pick a tailored resume and/or
an existing company-research record, then click **"Generate Interview
Preparation."**

## How questions are generated

Deterministic and evidence-based (`scripts/interview_prep.py`) — not
an LLM. Each category has a fixed question bank, triggered by keyword
matches against the job's text (e.g. the `kubernetes` category fires
on "kubernetes"/"k8s"/"eks"/"aks"/"gke"/"helm"). Real categories in the
code: Kubernetes, Cloud, Terraform/IaC, CI/CD, Observability, Incident
Management, SLI/SLO, Behavioral, Leadership, Company-specific,
Resume-based.

## Suggested answers

For each question, JobOS looks for a real quote from your own
confirmed resume/profile that demonstrates matching experience. If it
finds one, that's the suggested answer. **If it doesn't, it says so
honestly — "Preparation required" — rather than inventing an answer
you don't have.**

## Your own answers

Save your own answer, a confidence level, and notes per question —
these are yours, stored alongside the generated question, never
overwritten by regenerating prep for the same job.

## Recording the outcome

After the interview, record the real outcome (e.g. Scheduled,
Completed, Passed, Rejected, No Response). Two of these outcomes
(Rejected, No Response) can automatically advance the job's main
application status — see
[APPLICATION_TRACKER.md](APPLICATION_TRACKER.md)'s "Interview outcome"
section for the exact rule.

## Relationship to follow-ups

Interview outcome and follow-ups are independent — recording an
outcome doesn't automatically create a follow-up; schedule one
yourself from the application if you want a reminder (e.g. "send
interview thank-you") — see [FOLLOWUPS.md](FOLLOWUPS.md).
