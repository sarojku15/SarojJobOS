# Security Policy

For the full privacy/security model (candidate ownership, API key
handling, local-first architecture, public-deployment limitations),
see [docs/SECURITY.md](docs/SECURITY.md). This file covers
**vulnerability reporting** specifically.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**
Public issues are visible to everyone immediately, including before a
fix exists.

**This repository does not have a published, dedicated security
contact email at this time** — we're not going to invent one here.
The safest currently-available path:

1. Check whether this repository has GitHub's **private vulnerability
   reporting** enabled — look for a "Report a vulnerability" button
   under the repository's **Security** tab. If present, use it; it
   creates a private report visible only to maintainers.
2. If that option isn't available, contact the repository owner
   directly through their GitHub profile rather than the public issue
   tracker.

## What to never include in a report

- Real API keys or tokens (even partial/masked)
- Real candidate data or resumes
- A copy of a production database
- Passwords or credentials of any kind

Describe the vulnerability class and reproduction steps using
synthetic/fictional data only (see
[CONTRIBUTING.md](CONTRIBUTING.md)'s "Never commit" section for the
same convention applied to code contributions).

## Scope

This is a local-first, single-user-per-install application with no
built-in authentication layer (see [docs/SECURITY.md](docs/SECURITY.md)
for exactly what that does and doesn't protect against). The most
relevant classes of report are:
- A way to read/write another candidate's data without their
  `candidate_id` (an ownership-check bypass)
- A way to exfiltrate a stored provider API key in plaintext
- A path-traversal or injection vulnerability in resume upload,
  search input, or any other user-supplied field
- Anything that could make the app perform an action its own
  documentation says it never does (e.g. automatically submitting an
  application)
