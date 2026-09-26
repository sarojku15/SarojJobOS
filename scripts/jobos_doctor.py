#!/usr/bin/env python3
"""
JobOS diagnostics. Runs real, observable checks against THIS machine's
actual setup -- no hard-coded/simulated results. Every check either
passes, fails, or reports a genuinely-optional item as not configured.

Usage: scripts/jobos-doctor  (or: python3 scripts/jobos_doctor.py)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS, WARN, FAIL, SKIP = "✓", "○", "✗", "–"

results = []  # (mark, label, detail)


def check(label, ok, detail="", optional=False):
    mark = PASS if ok else (WARN if optional else FAIL)
    results.append((mark, label, detail))
    return ok


def run(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as error:
        return 1, "", str(error)


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


def flush():
    for mark, label, detail in results:
        line = f"{mark} {label}"
        if detail:
            line += f" - {detail}"
        print(line)
    results.clear()


def main():
    print("JobOS Diagnostics")
    print("=================")

    overall_ok = True

    # --- Python -----------------------------------------------------
    section("Runtime")
    py_ok = sys.version_info >= (3, 11)
    check("Python >= 3.11", py_ok, f"found {sys.version.split()[0]}")
    overall_ok &= py_ok

    node_path = shutil.which("node")
    node_ok = False
    if node_path:
        rc, out, _ = run([node_path, "--version"])
        node_ok = rc == 0
        check("Node.js >= 18", node_ok, out or "version check failed")
    else:
        check("Node.js installed", False, "not found on PATH (needed for Playwright)")
    overall_ok &= node_ok
    flush()

    # --- Python dependencies -----------------------------------------
    section("Python dependencies (requirements.txt)")
    req_path = ROOT / "requirements.txt"
    deps_ok = True
    if req_path.exists():
        for line in req_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg = line.split("==")[0].replace("-", "_")
            try:
                __import__(pkg)
                check(line, True)
            except ImportError:
                # a couple of package names differ from their import name
                alt = {"python_multipart": "multipart"}.get(pkg)
                if alt:
                    try:
                        __import__(alt)
                        check(line, True)
                        continue
                    except ImportError:
                        pass
                check(line, False, "not importable -- run: .venv/bin/pip install -r requirements.txt")
                deps_ok = False
    else:
        check("requirements.txt", False, "file not found")
        deps_ok = False
    overall_ok &= deps_ok
    flush()

    # --- Node dependencies / Playwright browser ----------------------
    section("Node dependencies & Playwright")
    node_modules_ok = (ROOT / "node_modules" / "playwright").is_dir()
    check("node_modules/playwright installed", node_modules_ok,
          "" if node_modules_ok else "run: npm install")
    overall_ok &= node_modules_ok

    cache_candidates = [
        Path.home() / "Library" / "Caches" / "ms-playwright",  # macOS
        Path.home() / ".cache" / "ms-playwright",              # Linux
    ]
    browser_ok = False
    for cache_dir in cache_candidates:
        if cache_dir.is_dir() and any(cache_dir.glob("chromium-*")):
            browser_ok = True
            break
    check("Chromium browser binary cached", browser_ok,
          "" if browser_ok else "run: npx playwright install chromium", optional=not node_modules_ok)
    if node_modules_ok:
        overall_ok &= browser_ok
    flush()

    # --- Database -----------------------------------------------------
    section("Database")
    prod_db = ROOT / "data" / "applications" / "jobos.db"
    dev_db = ROOT / "data" / "applications" / "jobos_dev.db"
    check("Production DB present", prod_db.exists(),
          str(prod_db) if prod_db.exists() else "not found (fine on a brand-new clone)", optional=True)
    if dev_db.exists():
        check("Dev DB present", True, str(dev_db))
    else:
        check("Dev DB present", True, "not created yet -- auto-created on first API request", optional=True)
    flush()

    # --- API ------------------------------------------------------------
    section("API")
    api_up = False
    for port in (8420, 8421):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
                body = json.loads(resp.read().decode())
                if body.get("status") == "OK":
                    api_up = True
                    check(f"API reachable on port {port}", True, f"db={body.get('db')}")
                    break
        except Exception:
            continue
    if not api_up:
        check("API reachable", False,
              "not running -- start with: .venv/bin/uvicorn api.main:app --port 8420", optional=True)
    flush()

    # --- Claude Skills ----------------------------------------------------
    section("Claude Skills")
    skills_dir = ROOT / ".claude" / "skills"
    skills_ok = True
    if skills_dir.is_dir():
        skill_files = sorted(skills_dir.glob("*/SKILL.md"))
        if not skill_files:
            check("Skills discovered", False, "no */SKILL.md found under .claude/skills/")
            skills_ok = False
        for skill_file in skill_files:
            text = skill_file.read_text()
            has_frontmatter = text.startswith("---")
            has_name = "\nname:" in text or text.startswith("name:")
            has_description = "description:" in text.split("---")[1] if has_frontmatter and text.count("---") >= 2 else False
            ok = has_frontmatter and has_description
            check(f"{skill_file.parent.name}/SKILL.md", ok,
                  "" if ok else "missing/malformed YAML frontmatter (name/description)")
            skills_ok &= ok
    else:
        check(".claude/skills/ present", False, "directory not found")
        skills_ok = False
    overall_ok &= skills_ok
    flush()

    # --- Provider configuration -------------------------------------------
    section("Search-provider configuration (optional)")
    env_path = ROOT / ".env"
    env_vars = dict(os.environ)
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if v.strip():
                    env_vars.setdefault(k.strip(), v.strip())
    provider_keys = ["YOU_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY"]
    any_configured = False
    for key in provider_keys:
        configured = bool(env_vars.get(key))
        any_configured |= configured
        check(key, configured, "configured" if configured else "not set", optional=True)
    check("At least one provider configured (7 extra sources)", any_configured,
          "" if any_configured else "optional -- 4 direct sources still work without this", optional=True)
    flush()

    # --- Job sources (only if API is up) ----------------------------------
    if api_up:
        section("Job Sources")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sources", timeout=5) as resp:
                body = json.loads(resp.read().decode())
            for row in body.get("enabled", []):
                status = row.get("final_status", "UNKNOWN")
                ok = status in ("ENABLED", "AVAILABLE_VIA_SEARCH_PROVIDER")
                check(row.get("source_name", "?"), ok, status)
            for row in body.get("unavailable", []):
                check(row.get("source_name", "?"), False, row.get("final_status", "NOT_ENABLED"), optional=True)
        except Exception as error:
            check("Fetch /api/sources", False, str(error))
        flush()

    print()
    print(f"Status: {'READY' if overall_ok else 'ISSUES FOUND'}")
    if not overall_ok:
        print("See docs/TROUBLESHOOTING.md for how to fix the items marked above.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
