#!/usr/bin/env python3

"""
Generates data/fixtures/resume_extractor/synthetic_sre_resume.pdf -- a
minimal, hand-built PDF (no reportlab/fpdf dependency; just the
standard base-14 Helvetica font, which pypdf.PdfReader.extract_text()
reads natively) containing a wholly fictional resume. Used by
scripts/test_resume_extractor.py's real-PDF end-to-end smoke test so
that test no longer depends on a private, gitignored resume
(resumes/SarojKumarNayak_SRE_DevOps_11Yrs.pdf).

Every fact in this fixture (name, email, employer, experience years)
is fictional -- never Saroj's real data -- while still exercising the
same extraction paths test 14 checks: email capture, an explicit
"N years of experience" summary phrase, exactly 4 employment entries,
and an unrecognized "KEY PROJECTS" section (proving it does not leak
into an employment entry's description).

Re-run this script only if the fixture's text content needs to change
-- the committed PDF is the actual test fixture; this script is how it
was produced, not something the test suite runs itself.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "data" / "fixtures" / "resume_extractor" / "synthetic_sre_resume.pdf"

RESUME_LINES = [
    "JORDAN SMITH",
    "Senior Site Reliability Engineer",
    "jordan.smith@example.com | 555-010-1234",
    "",
    "PROFESSIONAL SUMMARY",
    "Site Reliability Engineer with 9 years of experience across cloud",
    "infrastructure, Kubernetes, and CI/CD automation.",
    "",
    "TECHNICAL SKILLS",
    "Cloud Platforms: AWS, EKS, AKS, Azure",
    "Containers and Orchestration: Kubernetes, Docker, Helm",
    "Infrastructure as Code: Terraform, Ansible",
    "CI/CD: Jenkins, ArgoCD",
    "Observability and Monitoring: Prometheus, Grafana",
    "",
    "PROFESSIONAL EXPERIENCE",
    "Senior Site Reliability Engineer Jan 2023 - Present",
    "Example Analytics Inc",
    "- Defined SLOs and built observability dashboards.",
    "",
    "Site Reliability Engineer Feb 2021 - Dec 2022",
    "Sample Cloud Co",
    "- Migrated workloads to Kubernetes.",
    "",
    "DevOps Engineer Mar 2018 - Jan 2021",
    "Fictional Systems Ltd",
    "- Built CI/CD pipelines with Jenkins.",
    "",
    "Site Reliability Engineer Jun 2015 - Feb 2018",
    "Example Hosting Corp",
    "- Managed on-call rotations and incident response.",
    "",
    "KEY PROJECTS",
    "Disaster Recovery Automation",
    "- Automated failover, reducing recovery time by 60 percent.",
    "",
    "CERTIFICATIONS",
    "- Example Cloud Certification",
    "",
    "EDUCATION",
    "Bachelor of Technology - Computer Science",
    "Example University - 2014",
]


def _escape_pdf_string(text):
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf_bytes(lines):
    content_ops = ["BT", "/F1 10 Tf", "50 760 Td", "12 TL"]
    first = True
    for line in lines:
        escaped = _escape_pdf_string(line)
        if first:
            content_ops.append(f"({escaped}) Tj")
            first = False
        else:
            content_ops.append("T*")
            content_ops.append(f"({escaped}) Tj")
    content_ops.append("ET")
    content_stream = "\n".join(content_ops).encode("latin-1")

    objects = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objects[3] = (
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>"
    )
    objects[4] = (
        f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
        + content_stream
        + b"\nendstream"
    )
    objects[5] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    buf = bytearray()
    buf += b"%PDF-1.4\n"
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(buf)
        buf += f"{num} 0 obj\n".encode("latin-1")
        buf += objects[num]
        buf += b"\nendobj\n"

    xref_offset = len(buf)
    count = len(objects) + 1
    buf += f"xref\n0 {count}\n".encode("latin-1")
    buf += b"0000000000 65535 f \n"
    for num in sorted(objects):
        buf += f"{offsets[num]:010d} 00000 n \n".encode("latin-1")

    buf += b"trailer\n"
    buf += f"<< /Size {count} /Root 1 0 R >>\n".encode("latin-1")
    buf += b"startxref\n"
    buf += f"{xref_offset}\n".encode("latin-1")
    buf += b"%%EOF"

    return bytes(buf)


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(build_pdf_bytes(RESUME_LINES))
    print(f"Wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
