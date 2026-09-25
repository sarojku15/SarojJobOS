#!/usr/bin/env python3

"""
Phase 14.3 -- fix YouProvider's HTTP 403 (old, wrong API contract).

Root cause: the original implementation called the OLD, undocumented
GET https://api.ydc-index.io/search endpoint. You.com's CURRENT
documented Search API is POST https://ydc-index.io/v1/search, JSON
body {"query", "count"}, response shape results.web. Rewritten to
match, with YDC_API_KEY (You.com's own canonical env var name)
preferred and the project's original YOU_API_KEY kept as a fallback.

Fully offline: every HTTP call in this file is mocked
(unittest.mock.patch on urllib.request.urlopen) -- zero real network
requests, zero real API credits consumed, no real key used anywhere.
"""

import io
import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import hashlib

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


import search_provider as sp


class _FakeHTTPResponse:
    def __init__(self, body_dict):
        self._body = json.dumps(body_dict).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_http_error(code, body_dict=None, headers=None):
    body_bytes = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
    return urllib.error.HTTPError("https://ydc-index.io/v1/search", code, "error", headers or {}, io.BytesIO(body_bytes))


def _captured_request_urlopen(captured, response_or_error):
    def _urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        if isinstance(response_or_error, Exception):
            raise response_or_error
        return response_or_error
    return _urlopen


# ---------------------------------------------------------------------
# Endpoint / method / headers / request body
# ---------------------------------------------------------------------

_saved_ydc = os.environ.pop("YDC_API_KEY", None)
_saved_you = os.environ.pop("YOU_API_KEY", None)
try:
    os.environ["YDC_API_KEY"] = "fake-ydc-test-key-0000"
    captured = {}
    fake_response = _FakeHTTPResponse({"results": {"web": []}})
    with patch("urllib.request.urlopen", side_effect=_captured_request_urlopen(captured, fake_response)):
        provider = sp.YouProvider()
        provider.search("site reliability engineer Bangalore", num=10, recency=None)

    check(captured["url"] == "https://ydc-index.io/v1/search", f"endpoint: uses the CURRENT documented POST /v1/search endpoint (got {captured['url']!r})")
    check(captured["url"] != "https://api.ydc-index.io/search", "endpoint: does NOT use the old, wrong api.ydc-index.io/search endpoint")
    check(captured["method"] == "POST", f"method: uses POST, not the old GET (got {captured['method']!r})")
    check(captured["headers"].get("X-api-key") == "fake-ydc-test-key-0000" or captured["headers"].get("X-Api-key") == "fake-ydc-test-key-0000", f"auth header: X-API-Key is sent with the configured key (got headers={captured['headers']})")
    check(captured["headers"].get("Content-type") == "application/json", "headers: Content-Type: application/json is sent")
    body = json.loads(captured["body"])
    check(body.get("query") == "site reliability engineer Bangalore", f"request body: query is preserved verbatim (got {body})")
    check(body.get("count") == 10, f"request body: uses 'count' as the result-count parameter, per You.com's current docs (got {body})")
finally:
    os.environ.pop("YDC_API_KEY", None)
    os.environ.pop("YOU_API_KEY", None)
    if _saved_ydc is not None:
        os.environ["YDC_API_KEY"] = _saved_ydc
    if _saved_you is not None:
        os.environ["YOU_API_KEY"] = _saved_you


# ---------------------------------------------------------------------
# Response parsing from results.web
# ---------------------------------------------------------------------

_saved_you2 = os.environ.pop("YOU_API_KEY", None)
os.environ.pop("YDC_API_KEY", None)
try:
    os.environ["YOU_API_KEY"] = "fake-you-test-key-1111"

    real_shaped_response = _FakeHTTPResponse(
        {
            "results": {
                "web": [
                    {"url": "https://in.linkedin.com/jobs/view/senior-sre-4012345678", "title": "Senior SRE at Acme", "description": "Great SRE role at Acme", "age": "3 days ago"},
                    {"url": "https://in.linkedin.com/jobs/view/sre-3999999999", "title": "SRE at Beta Corp", "snippets": ["snippet text"], "published-date": "2026-09-18"},
                ]
            }
        }
    )
    with patch("urllib.request.urlopen", return_value=real_shaped_response):
        provider = sp.YouProvider()
        results = provider.search("site reliability engineer", num=5, recency=None)

    check(len(results) == 2, f"parsing: correct number of results extracted from results.web (got {len(results)})")
    check(results[0]["url"] == "https://in.linkedin.com/jobs/view/senior-sre-4012345678", "parsing: url field mapped correctly")
    check(results[0]["title"] == "Senior SRE at Acme", "parsing: title field mapped correctly")
    check(results[0]["snippet"] == "Great SRE role at Acme", "parsing: description -> snippet mapped correctly")
    check(results[0]["date"] == "3 days ago", "parsing: age -> date mapped correctly")
    check(results[1]["date"] == "2026-09-18", "parsing: published-date -> date mapped correctly when age is absent")
    check(set(results[0].keys()) == {"title", "url", "snippet", "date"}, f"parsing: never invents extra fields beyond the common shape (got {set(results[0].keys())})")

    # Empty result set -- a SUCCESSFUL search, not a provider failure.
    empty_response = _FakeHTTPResponse({"results": {"web": []}})
    with patch("urllib.request.urlopen", return_value=empty_response):
        results = sp.YouProvider().search("a query with no matches", num=5, recency=None)
    check(results == [], "empty results: an empty results.web list returns [] cleanly, no exception raised")

    # Missing results.web entirely -- handled safely, never crashes.
    for malformed in [{}, {"results": {}}, {"results": None}, {"results": {"web": None}}, {"results": {"web": "not-a-list"}}]:
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(malformed)):
            results = sp.YouProvider().search("q", num=5, recency=None)
        check(results == [], f"malformed/missing results.web ({malformed!r}) handled safely, returns [] rather than crashing")
finally:
    os.environ.pop("YOU_API_KEY", None)
    if _saved_you2 is not None:
        os.environ["YOU_API_KEY"] = _saved_you2


# ---------------------------------------------------------------------
# Error classification: 401, 403 (+ missing-scope diagnostic), 429,
# 402, 5xx, timeout -- using search_provider.ProviderErrorType.
# ---------------------------------------------------------------------

_saved_you3 = os.environ.pop("YOU_API_KEY", None)
os.environ.pop("YDC_API_KEY", None)
try:
    os.environ["YOU_API_KEY"] = "fake-you-test-key-2222"

    with patch("urllib.request.urlopen", side_effect=_fake_http_error(401, {"message": "Invalid API key"})):
        try:
            sp.YouProvider().search("q", num=1, recency=None)
            check(False, "401 must raise ProviderSearchError")
        except sp.ProviderSearchError as error:
            check(error.error_type == sp.ProviderErrorType.AUTH_FAILED, f"401 classified as AUTH_FAILED (got {error.error_type})")

    with patch("urllib.request.urlopen", side_effect=_fake_http_error(403, {"message": "Missing required scopes"})):
        try:
            sp.YouProvider().search("q", num=1, recency=None)
            check(False, "403 must raise ProviderSearchError")
        except sp.ProviderSearchError as error:
            check(error.error_type == sp.ProviderErrorType.AUTH_FAILED, f"403 classified as AUTH_FAILED (got {error.error_type})")
            check("Missing required scopes" in error.detail, f"403 missing-scope diagnostic: the provider's own response message is captured in .detail (got {error.detail!r})")
            check("fake-you-test-key-2222" not in error.detail, "403 diagnostic never leaks the API key")

    # A 403 with no parseable body still classifies correctly, just
    # without an extra message -- never crashes on a malformed/empty
    # error body.
    with patch("urllib.request.urlopen", side_effect=_fake_http_error(403, None)):
        try:
            sp.YouProvider().search("q", num=1, recency=None)
            check(False, "403 with no body must still raise ProviderSearchError")
        except sp.ProviderSearchError as error:
            check(error.error_type == sp.ProviderErrorType.AUTH_FAILED and "HTTP 403" in error.detail, f"403 with no body still classifies correctly (got {error.error_type}, {error.detail!r})")

    with patch("urllib.request.urlopen", side_effect=_fake_http_error(429, {"message": "Too many requests"})):
        try:
            sp.YouProvider().search("q", num=1, recency=None)
            check(False, "429 must raise ProviderSearchError")
        except sp.ProviderSearchError as error:
            check(error.error_type == sp.ProviderErrorType.RATE_LIMITED, f"429 classified as RATE_LIMITED (got {error.error_type})")

    with patch("urllib.request.urlopen", side_effect=_fake_http_error(402, {"message": "Quota exceeded"})):
        try:
            sp.YouProvider().search("q", num=1, recency=None)
            check(False, "402 must raise ProviderSearchError")
        except sp.ProviderSearchError as error:
            check(error.error_type == sp.ProviderErrorType.QUOTA_EXHAUSTED, f"402 classified as QUOTA_EXHAUSTED (got {error.error_type})")

    with patch("time.sleep", return_value=None):
        for code in (500, 502, 503, 504):
            with patch("urllib.request.urlopen", side_effect=_fake_http_error(code, {"message": "server error"})):
                try:
                    sp.YouProvider().search("q", num=1, recency=None)
                    check(False, f"{code} must raise ProviderSearchError after retries")
                except sp.ProviderSearchError as error:
                    check(error.error_type == sp.ProviderErrorType.SERVER_ERROR, f"{code} classified as SERVER_ERROR (got {error.error_type})")

    with patch("time.sleep", return_value=None):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            try:
                sp.YouProvider().search("q", num=1, recency=None)
                check(False, "timeout must raise ProviderSearchError after retries")
            except sp.ProviderSearchError as error:
                check(error.error_type == sp.ProviderErrorType.TIMEOUT, f"timeout classified as TIMEOUT (got {error.error_type})")

    with patch("time.sleep", return_value=None):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("network unreachable")):
            try:
                sp.YouProvider().search("q", num=1, recency=None)
                check(False, "network failure must raise ProviderSearchError after retries")
            except sp.ProviderSearchError as error:
                check(error.error_type == sp.ProviderErrorType.NETWORK_ERROR, f"network failure classified as NETWORK_ERROR (got {error.error_type})")
finally:
    os.environ.pop("YOU_API_KEY", None)
    if _saved_you3 is not None:
        os.environ["YOU_API_KEY"] = _saved_you3


# ---------------------------------------------------------------------
# Freshness (Part 4, updated by Phase 14.4's live-validation finding):
# a live diagnostic against the REAL You.com API confirmed that
# sending their "freshness" request parameter (e.g. "week", the
# originally-planned mapping for this project's default max_job_age_
# days window) returns ZERO results for a query that otherwise returns
# real results with the parameter omitted -- reproduced consistently.
# YouProvider therefore deliberately never sends "freshness" for now,
# regardless of the recency token given -- verified here for every
# token, including the ones that previously WOULD have triggered it.
# ---------------------------------------------------------------------

_saved_you4 = os.environ.pop("YOU_API_KEY", None)
os.environ.pop("YDC_API_KEY", None)
try:
    os.environ["YOU_API_KEY"] = "fake-you-test-key-3333"
    for recency_token in ["qdr:d", "qdr:w", "qdr:m", None, "unrecognized-token"]:
        captured = {}
        with patch("urllib.request.urlopen", side_effect=_captured_request_urlopen(captured, _FakeHTTPResponse({"results": {"web": []}}))):
            sp.YouProvider().search("q", num=1, recency=recency_token)
        body = json.loads(captured["body"])
        check("freshness" not in body, f"freshness: never sent to You.com regardless of recency token (Phase 14.4 live-evidenced fix -- got recency={recency_token!r}, body={body!r})")
finally:
    os.environ.pop("YOU_API_KEY", None)
    if _saved_you4 is not None:
        os.environ["YOU_API_KEY"] = _saved_you4


# ---------------------------------------------------------------------
# YDC_API_KEY / YOU_API_KEY fallback + no-key handling
# ---------------------------------------------------------------------

_saved_ydc2 = os.environ.pop("YDC_API_KEY", None)
_saved_you5 = os.environ.pop("YOU_API_KEY", None)
try:
    check(sp.is_provider_configured("you") is False, "no key: is_provider_configured('you') is False with neither var set")
    try:
        sp.YouProvider()
        check(False, "no key: YouProvider() must refuse to construct")
    except RuntimeError as error:
        check("fake-you-test-key" not in str(error) and "YDC" in str(error), f"no key: RuntimeError mentions the expected env var names, never a key value (got {error})")

    os.environ["YOU_API_KEY"] = "fake-legacy-fallback-key-4444"
    check(sp.is_provider_configured("you") is True, "fallback: YOU_API_KEY alone is still accepted (backward compatibility)")
    provider = sp.YouProvider()
    check(provider.api_key == "fake-legacy-fallback-key-4444", "fallback: YouProvider reads the legacy YOU_API_KEY when YDC_API_KEY is absent")

    os.environ["YDC_API_KEY"] = "fake-preferred-key-5555"
    check(sp.get_active_env_key_name("you") == "YDC_API_KEY", "preference: YDC_API_KEY is preferred over YOU_API_KEY when both are set")
    provider2 = sp.YouProvider()
    check(provider2.api_key == "fake-preferred-key-5555", "preference: YouProvider uses YDC_API_KEY's value when both are set")
finally:
    os.environ.pop("YDC_API_KEY", None)
    os.environ.pop("YOU_API_KEY", None)
    if _saved_ydc2 is not None:
        os.environ["YDC_API_KEY"] = _saved_ydc2
    if _saved_you5 is not None:
        os.environ["YOU_API_KEY"] = _saved_you5


# ---------------------------------------------------------------------
# No key leakage into exceptions/logs
# ---------------------------------------------------------------------

_saved_you6 = os.environ.pop("YOU_API_KEY", None)
os.environ.pop("YDC_API_KEY", None)
try:
    os.environ["YOU_API_KEY"] = "super-secret-should-never-appear-XYZ"
    with patch("urllib.request.urlopen", side_effect=_fake_http_error(403, {"message": "Missing required scopes"})):
        try:
            sp.YouProvider().search("q", num=1, recency=None)
        except sp.ProviderSearchError as error:
            check("super-secret-should-never-appear-XYZ" not in str(error), "no leakage: the API key never appears in the ProviderSearchError's string representation")
            check("super-secret-should-never-appear-XYZ" not in error.detail, "no leakage: the API key never appears in .detail")
    try:
        os.environ.pop("YOU_API_KEY")
        os.environ.pop("YDC_API_KEY", None)
        sp.YouProvider()
    except RuntimeError as error:
        check("super-secret" not in str(error), "no leakage: a construction-time RuntimeError never contains a key value")
finally:
    os.environ.pop("YOU_API_KEY", None)
    os.environ.pop("YDC_API_KEY", None)
    if _saved_you6 is not None:
        os.environ["YOU_API_KEY"] = _saved_you6


production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
