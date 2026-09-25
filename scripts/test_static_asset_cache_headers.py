#!/usr/bin/env python3

"""
Regression test for a real, observed bug: web/app.js and every
web/*.html page were served with no Cache-Control header at all, so a
browser's own default heuristic caching could serve a STALE cached
/static/app.js on an ordinary (non-hard) refresh even after the file
on disk changed -- observed live as "SOURCE_STATUS_LABELS is not
defined" on the results page after a shared const was moved into
app.js (the served HTML was fresh, the cached JS was not).

Fix: api/main.py's _no_cache_for_static_assets middleware sets
Cache-Control: no-cache (revalidate-always, not "never cache" -- an
unchanged file still gets a cheap 304 via the existing ETag/
Last-Modified) on every HTML/JS/CSS response, scoped by content-type
so JSON API responses are never touched.

Exercises api/main.py's FastAPI app in-process via TestClient. Fully
offline, isolated temp DB (never jobos.db/jobos_dev.db). No network.
"""

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS: {message}")
    else:
        failed += 1
        print(f"FAIL: {message}")


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_cache_headers_"))
tmp_db = tmp_dir / "jobos_test.db"

import init_dev_db
init_dev_db.init_dev_db(tmp_db)

import db as db_mod
db_mod.DEV_DB = tmp_db

from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db

client = TestClient(api_main.app)

# --- 1. the actual asset that broke: /static/app.js must always revalidate ---
resp = client.get("/static/app.js")
check(resp.status_code == 200, "1. GET /static/app.js -> 200")
check(resp.headers.get("cache-control") == "no-cache", f"1. /static/app.js has Cache-Control: no-cache (got {resp.headers.get('cache-control')!r}) -- the exact fix for the observed staleness bug")

# --- 2. every other static JS/CSS asset gets the same treatment ---
for path in ["/static/style.css"]:
    resp = client.get(path)
    check(resp.headers.get("cache-control") == "no-cache", f"2. {path} has Cache-Control: no-cache (got {resp.headers.get('cache-control')!r})")

# --- 3. every HTML page route gets the same treatment (not just app.js) ---
for path in ["/", "/dashboard", "/profile", "/searches", "/searches/new", "/import"]:
    resp = client.get(path)
    check(resp.headers.get("cache-control") == "no-cache", f"3. {path} (HTML page) has Cache-Control: no-cache (got {resp.headers.get('cache-control')!r})")

# --- 4. JSON API responses are UNCHANGED -- this fix must not add
#     no-cache noise to API traffic that never had the bug ---
resp = client.get("/api/health")
check("cache-control" not in {k.lower() for k in resp.headers.keys()}, f"4. /api/health (JSON) has NO Cache-Control header added (got {resp.headers.get('cache-control')!r}) -- the fix is scoped to HTML/JS/CSS only")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
