#!/usr/bin/env python3

"""
Search-provider abstraction (Phase 14, extended in Phase 14.2). A real,
third-party web-search API used to discover public job-detail URLs on
sites this project has no authorized direct-crawl access to (see
restricted_source_registry.py and CLAUDE.md's Source Adapter
Principle). This module never fetches LinkedIn/Indeed/Foundit/
Instahyre/Cutshort/Wellfound/Shine directly -- it only talks to each
search-provider's own API.

Phase 14.2 adds four more providers (You.com, Tavily, Exa, Brave)
alongside the original Serper -- SerperProvider is NOT deleted or
redesigned, only joined by siblings implementing the exact same
SearchProvider interface. search_provider_manager.py (new this phase)
is what actually selects among them with failover; this module only
defines what a provider IS and how it talks to its own API.

Every *_API_KEY is read from the environment ONLY -- never hardcoded,
never committed, never logged, never included in an error message.
If a key is absent, that provider is simply never constructed (see
is_provider_configured()); nothing in this module makes a live request
or fabricates a result when a key is missing.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

# Root-cause fix: load .env into os.environ HERE, at this module's own
# import time, before is_provider_configured()/get_configured_providers()
# below are ever called by anything -- including
# search_provider_adapter.py's module-level `status` class attribute,
# which is computed once at ITS first import and would otherwise see
# an empty environment whenever this module is reached through an
# entrypoint that never loaded .env itself (scripts/submit_search.py,
# scripts/run_search_worker.py, or any future CLI script -- api/main.py
# already did this inline, but relying on every current AND future
# entrypoint to remember that is exactly how the 7 search-provider
# boards silently stopped participating in a CLI-submitted search
# while the API server's own searches kept working). Idempotent
# (setdefault) and safe even when api/main.py's own loading also runs.
import env_config as _env_config

_env_config.load_env_file_into_environ()


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, num: int = 10, recency: str | None = None) -> list[dict]: ...


class ProviderErrorType(Enum):
    """Classification of a failed provider call -- this is what
    search_provider_manager.SearchProviderManager actually branches on
    to decide retry-same-provider vs. move-to-next-provider (Part 5)."""

    AUTH_FAILED = "AUTH_FAILED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    UNKNOWN = "UNKNOWN"


class ProviderSearchError(Exception):
    """Raised by any provider's .search() on a failure that survived
    that provider's own internal retry loop. Never raised for an empty
    (but successful) result list -- an empty list is a normal, valid
    return value, never an error (Part 5's explicit "do not switch
    provider merely because results are empty")."""

    def __init__(self, provider_name, error_type: ProviderErrorType, detail: str = "", retry_after: float | None = None):
        self.provider_name = provider_name
        self.error_type = error_type
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(f"{provider_name}: {error_type.value}" + (f" -- {detail}" if detail else ""))


@dataclass
class ProviderError:
    query: str
    error_type: str
    detail: str


# ---------------------------------------------------------------------
# Configuration -- environment variables only, never hardcoded
# ---------------------------------------------------------------------

PROVIDER_ENV_KEYS = {
    "you": "YOU_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
    "brave": "BRAVE_API_KEY",
    "serper": "SERPER_API_KEY",
}

# Phase 14.3: You.com's own current documentation calls its canonical
# variable YDC_API_KEY -- this project's existing GUI/Settings surface
# still WRITES the original YOU_API_KEY (PROVIDER_ENV_KEYS above is
# still the save/mask/display target -- no GUI migration), but READING
# must accept either name, YDC_API_KEY preferred. A provider not listed
# here has exactly one accepted name (PROVIDER_ENV_KEYS[name]).
PROVIDER_ENV_KEY_ALIASES = {
    "you": ["YDC_API_KEY", "YOU_API_KEY"],
}

DEFAULT_PROVIDER_ORDER = ["you", "tavily", "exa", "brave", "serper"]

# ADVISORY ONLY (Phase 14.2, Part 7) -- current public free-tier claims
# for documentation/GUI display purposes. Runtime logic (quota
# enforcement, failover) NEVER reads these values or assumes they are
# still accurate -- see search_provider_usage_store.py, which treats
# every local counter as an advisory local budget, never an assertion
# of a provider's actual remaining quota. These may change at any time
# without this project being told.
PROVIDER_ADVISORY_FREE_TIER = {
    "you": "100 Web Search queries/day free, per You.com's current public pricing (as of this project's last check -- verify before relying on it).",
    "tavily": "1,000 API credits/month free, per Tavily's current public pricing.",
    "exa": "A free allocation/credits are available on Exa's current starter plan (exact amount not pinned here -- check Exa's own pricing page).",
    "brave": "Monthly promotional/free credits available on Brave Search API; card verification may be required.",
    "serper": "2,500 initial free queries currently advertised by Serper.dev.",
}


def _env_key_candidates(provider_name: str) -> list[str]:
    """All accepted env var names for a provider, in preference order
    -- PROVIDER_ENV_KEY_ALIASES when the provider has more than one
    (e.g. "you": YDC_API_KEY preferred, YOU_API_KEY as a fallback for
    backward compatibility), else just PROVIDER_ENV_KEYS[name]."""
    if provider_name in PROVIDER_ENV_KEY_ALIASES:
        return list(PROVIDER_ENV_KEY_ALIASES[provider_name])
    env_key = PROVIDER_ENV_KEYS.get(provider_name)
    return [env_key] if env_key else []


def get_provider_api_key(provider_name: str) -> str | None:
    """The actual configured key value for a provider, checking every
    accepted env var name in preference order -- None if none is set.
    Never logs or prints the value; callers pass it straight to their
    own provider's __init__."""
    for env_key in _env_key_candidates(provider_name):
        value = os.environ.get(env_key, "").strip()
        if value:
            return value
    return None


def is_provider_configured(provider_name: str) -> bool:
    """Pure environment check -- no network call. The single source of
    truth for whether a given provider CAN be constructed at all."""
    return get_provider_api_key(provider_name) is not None


def get_active_env_key_name(provider_name: str) -> str:
    """Which env var name is ACTUALLY set for this provider right now
    (checked in preference order), for accurate GUI display (Phase
    14.3) -- e.g. reports "YDC_API_KEY" if that's the one a user set,
    even though the GUI's own Save action still writes the canonical
    PROVIDER_ENV_KEYS[name] ("YOU_API_KEY"). Falls back to
    PROVIDER_ENV_KEYS[name] (the canonical/save-target name) when
    nothing is currently set, so the GUI always has a name to show."""
    for env_key in _env_key_candidates(provider_name):
        if os.environ.get(env_key, "").strip():
            return env_key
    return PROVIDER_ENV_KEYS.get(provider_name, "")


def is_serper_configured() -> bool:
    """Backward-compatible alias (Phase 14) -- unchanged behavior."""
    return is_provider_configured("serper")


def get_configured_providers() -> list[str]:
    """Every provider name (from PROVIDER_ENV_KEYS) with a non-empty
    API key currently set in the environment, in DEFAULT_PROVIDER_ORDER
    -- never a guess, never including one without a real key."""
    return [name for name in DEFAULT_PROVIDER_ORDER if is_provider_configured(name)]


# ---------------------------------------------------------------------
# Shared HTTP + retry + error-classification helper
# ---------------------------------------------------------------------

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _classify_status(status_code: int) -> ProviderErrorType:
    """Generic, provider-agnostic default classification. A provider
    whose API signals quota exhaustion with a distinct code (e.g. 402)
    overrides this in its own _classify_status()."""
    if status_code in (401, 403):
        return ProviderErrorType.AUTH_FAILED
    if status_code == 429:
        return ProviderErrorType.RATE_LIMITED
    if status_code == 402:
        return ProviderErrorType.QUOTA_EXHAUSTED
    if status_code >= 500:
        return ProviderErrorType.SERVER_ERROR
    return ProviderErrorType.UNKNOWN


_MAX_ERROR_DETAIL_CHARS = 300


def _safe_error_detail(error):
    """Extracts a short, safe diagnostic snippet from an HTTPError's
    own RESPONSE body (Phase 14.3, Part 6) -- e.g. You.com's own
    "Missing required scopes" message on a 403. This is what the
    SERVER sent back, never anything from our own request (so it can
    never contain our API key, which is a REQUEST header we set, not
    something the server echoes) -- truncated defensively regardless,
    and never returns raw bytes/headers, only readable text."""
    try:
        raw = error.read()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("message", "error", "detail", "error_message", "reason"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
    except (json.JSONDecodeError, TypeError):
        pass

    text = text.strip()
    if len(text) > _MAX_ERROR_DETAIL_CHARS:
        text = text[:_MAX_ERROR_DETAIL_CHARS] + "..."
    return text


def _http_request_with_retry(
    provider_name: str,
    method: str,
    url: str,
    headers: dict,
    body: bytes | None,
    timeout: int,
    retry_count: int,
    classify_status=_classify_status,
):
    """One shared request+retry+classify loop every provider below
    uses -- avoids five copies of the same retry logic. Returns the
    parsed JSON response body on success. Raises ProviderSearchError
    (never a raw urllib exception) on any failure that survives
    `retry_count` attempts, with the classified ProviderErrorType the
    manager needs to decide retry-vs-failover, and a safe, readable
    detail string (the provider's own response body/message when one
    was returned, e.g. "Missing required scopes" -- Phase 14.3)."""
    last_error_type = ProviderErrorType.UNKNOWN
    last_detail = ""

    for attempt in range(max(1, retry_count)):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            error_type = classify_status(error.code)
            last_error_type = error_type
            safe_detail = _safe_error_detail(error)
            last_detail = f"HTTP {error.code}" + (f" -- {safe_detail}" if safe_detail else "")

            if error_type == ProviderErrorType.AUTH_FAILED:
                # Never worth retrying -- a bad/revoked key doesn't
                # become valid on the next attempt.
                raise ProviderSearchError(provider_name, error_type, last_detail) from error

            if error_type == ProviderErrorType.QUOTA_EXHAUSTED:
                raise ProviderSearchError(provider_name, error_type, last_detail) from error

            if error_type == ProviderErrorType.RATE_LIMITED:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                wait_seconds = None
                if retry_after:
                    try:
                        wait_seconds = min(float(retry_after), 10.0)
                    except ValueError:
                        wait_seconds = None
                if attempt < retry_count - 1:
                    time.sleep(wait_seconds if wait_seconds is not None else 2**attempt)
                    continue
                raise ProviderSearchError(provider_name, error_type, last_detail, retry_after=wait_seconds) from error

            if error.code in _RETRYABLE_STATUS and attempt < retry_count - 1:
                time.sleep(2**attempt)
                continue

            raise ProviderSearchError(provider_name, error_type, last_detail) from error

        except TimeoutError as error:
            last_error_type = ProviderErrorType.TIMEOUT
            last_detail = str(error)
            if attempt < retry_count - 1:
                time.sleep(2**attempt)
                continue
            raise ProviderSearchError(provider_name, ProviderErrorType.TIMEOUT, last_detail) from error

        except (urllib.error.URLError, OSError) as error:
            last_error_type = ProviderErrorType.NETWORK_ERROR
            last_detail = str(error)
            if attempt < retry_count - 1:
                time.sleep(2**attempt)
                continue
            raise ProviderSearchError(provider_name, ProviderErrorType.NETWORK_ERROR, last_detail) from error

        except json.JSONDecodeError as error:
            # response.read() succeeded (no network exception above) but
            # the body wasn't valid JSON -- e.g. a proxy/gateway error
            # page or a truncated/garbled body. Previously escaped this
            # function as a raw JSONDecodeError (a ValueError subclass,
            # not matched by any except above), propagating out of
            # SearchProviderManager/search_provider_adapter uncaught and
            # aborting the entire multi-source search batch instead of
            # being isolated to this one provider/query. Same
            # retry-then-raise shape as NETWORK_ERROR above -- a retry
            # may simply get a well-formed body next time.
            last_error_type = ProviderErrorType.UNKNOWN
            last_detail = f"malformed JSON response: {error}"
            if attempt < retry_count - 1:
                time.sleep(2**attempt)
                continue
            raise ProviderSearchError(provider_name, ProviderErrorType.UNKNOWN, last_detail) from error

    raise ProviderSearchError(provider_name, last_error_type, last_detail)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


_DEFAULT_TIMEOUT = _int_env("SEARCH_PROVIDER_TIMEOUT", 20)
_DEFAULT_RETRY_COUNT = _int_env("SEARCH_PROVIDER_RETRY_COUNT", 3)


# ---------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------

class SerperProvider:
    """Google results via Serper.dev (https://serper.dev). Unchanged
    from Phase 14 except for reusing the new shared retry helper
    internally -- its public interface/behavior is identical."""

    name = "serper"
    URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str | None = None, gl: str = "in", timeout: int | None = None):
        api_key = api_key or os.environ.get("SERPER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "SerperProvider requires SERPER_API_KEY (constructor arg or "
                "environment variable) -- never construct this class "
                "speculatively; check is_provider_configured('serper') first."
            )
        self.api_key = api_key
        self.gl = os.environ.get("SERPER_GL", gl)
        self.timeout = int(os.environ.get("SERPER_TIMEOUT", timeout or _DEFAULT_TIMEOUT))
        self.retry_count = _DEFAULT_RETRY_COUNT

    def search(self, query: str, num: int = 10, recency: str | None = "qdr:w") -> list[dict]:
        body = {"q": query, "gl": self.gl, "num": num}
        if recency:
            body["tbs"] = recency

        payload = _http_request_with_retry(
            self.name, "POST", self.URL,
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
            timeout=self.timeout, retry_count=self.retry_count,
        )
        return [
            {"title": o.get("title", ""), "url": o.get("link", ""), "snippet": o.get("snippet", ""), "date": o.get("date")}
            for o in payload.get("organic", [])
        ]


class YouProvider:
    """You.com Web Search API. Phase 14.3 fix: the previous
    implementation used the OLD GET https://api.ydc-index.io/search
    endpoint, which returns HTTP 403 against a current You.com key --
    rewritten against You.com's CURRENT documented Search API:
    POST https://ydc-index.io/v1/search, X-API-Key header, JSON body
    {"query", "count"}, response shape results.web (a list of
    {url, title, description, ...}).

    Reads YDC_API_KEY first (You.com's own canonical env var name per
    their current docs), falling back to this project's original
    YOU_API_KEY for backward compatibility -- see
    search_provider.get_provider_api_key(). The existing Settings GUI
    still WRITES YOU_API_KEY (no migration of the save path), but
    reading now accepts either name."""

    name = "you"
    URL = "https://ydc-index.io/v1/search"

    # Serper-shaped qdr: tokens (what search_provider_adapter.py's
    # shared _recency_filter() produces for every provider today,
    # Serper included -- see that module's own docstring) translated
    # into You.com's own freshness vocabulary (Phase 14.3, Part 4: 1
    # day -> "day", 7 days -> "week", 30 days -> "month"). An
    # unrecognized/absent value omits the freshness param entirely
    # rather than guessing -- You.com's "week" filter is never treated
    # as proof of JobOS's <=3-day requirement; real freshness is still
    # always re-derived downstream by freshness.classify_freshness().
    _RECENCY_TO_FRESHNESS = {"qdr:d": "day", "qdr:w": "week", "qdr:m": "month"}

    def __init__(self, api_key: str | None = None, timeout: int | None = None):
        api_key = api_key or get_provider_api_key("you")
        if not api_key:
            raise RuntimeError("YouProvider requires YDC_API_KEY (preferred) or YOU_API_KEY -- check is_provider_configured('you') first.")
        self.api_key = api_key
        self.timeout = timeout or _DEFAULT_TIMEOUT
        self.retry_count = _DEFAULT_RETRY_COUNT

    def search(self, query: str, num: int = 10, recency: str | None = None) -> list[dict]:
        body = {"query": query, "count": num}
        # Phase 14.4 live-validation finding: sending You.com's own
        # "freshness" request parameter (e.g. freshness="week", the
        # mapped value for this project's default max_job_age_days
        # window) was confirmed, via a minimal live diagnostic against
        # the real API, to return ZERO results for a query that
        # otherwise returns real results with the parameter omitted --
        # reproduced consistently, not a fluke. Since the exact correct
        # contract for this parameter (name/value/placement) hasn't
        # been separately verified against You.com's current docs
        # beyond this project's own reading, and shipping a parameter
        # that silently empties every result would be strictly worse
        # than a narrower-but-working freshness filter, this is
        # deliberately NOT sent for now -- the mapping table above is
        # kept as-is (harmless, useful reference for a future,
        # dedicated investigation) but its result is intentionally
        # unused here. Freshness is still correctly determined
        # downstream by the existing, unmodified freshness.classify_
        # freshness() from each result's own date text, exactly as
        # before -- this only affects the provider-side request hint,
        # never JobOS's own freshness classification.
        freshness = self._RECENCY_TO_FRESHNESS.get(recency)  # noqa: F841 -- computed, deliberately unused; see note above

        payload = _http_request_with_retry(
            self.name, "POST", self.URL,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
            timeout=self.timeout, retry_count=self.retry_count,
        )
        # results.web -- handled defensively: missing/null/non-list
        # all safely yield []. An empty (but successful) result list is
        # NOT a provider failure (Part 3/7) -- this function simply
        # returns it as-is; SearchProviderManager decides what an
        # empty result means, this class never does.
        results = payload.get("results") if isinstance(payload, dict) else None
        web_results = results.get("web") if isinstance(results, dict) else None
        if not isinstance(web_results, list):
            web_results = []

        return [
            {
                "title": r.get("title", "") if isinstance(r, dict) else "",
                "url": r.get("url", "") if isinstance(r, dict) else "",
                "snippet": (r.get("description") or r.get("snippet") or "") if isinstance(r, dict) else "",
                "date": (r.get("age") or r.get("published-date") or r.get("published_date")) if isinstance(r, dict) else None,
            }
            for r in web_results
        ]


class TavilyProvider:
    """Tavily Search API (https://tavily.com)."""

    name = "tavily"
    URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None, timeout: int | None = None):
        api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TavilyProvider requires TAVILY_API_KEY -- check is_provider_configured('tavily') first.")
        self.api_key = api_key
        self.timeout = timeout or _DEFAULT_TIMEOUT
        self.retry_count = _DEFAULT_RETRY_COUNT

    def search(self, query: str, num: int = 10, recency: str | None = None) -> list[dict]:
        body = {"api_key": self.api_key, "query": query, "max_results": num, "include_answer": False}
        payload = _http_request_with_retry(
            self.name, "POST", self.URL,
            headers={"Content-Type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
            timeout=self.timeout, retry_count=self.retry_count,
        )
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", ""), "date": r.get("published_date")}
            for r in payload.get("results", [])
        ]


class ExaProvider:
    """Exa Search API (https://exa.ai)."""

    name = "exa"
    URL = "https://api.exa.ai/search"

    def __init__(self, api_key: str | None = None, timeout: int | None = None):
        api_key = api_key or os.environ.get("EXA_API_KEY")
        if not api_key:
            raise RuntimeError("ExaProvider requires EXA_API_KEY -- check is_provider_configured('exa') first.")
        self.api_key = api_key
        self.timeout = timeout or _DEFAULT_TIMEOUT
        self.retry_count = _DEFAULT_RETRY_COUNT

    def search(self, query: str, num: int = 10, recency: str | None = None) -> list[dict]:
        body = {"query": query, "numResults": num, "type": "keyword"}
        payload = _http_request_with_retry(
            self.name, "POST", self.URL,
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
            timeout=self.timeout, retry_count=self.retry_count,
        )
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("text", "")[:500] if r.get("text") else "", "date": r.get("publishedDate")}
            for r in payload.get("results", [])
        ]


class BraveProvider:
    """Brave Search API (https://api.search.brave.com)."""

    name = "brave"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None, timeout: int | None = None):
        api_key = api_key or os.environ.get("BRAVE_API_KEY")
        if not api_key:
            raise RuntimeError("BraveProvider requires BRAVE_API_KEY -- check is_provider_configured('brave') first.")
        self.api_key = api_key
        self.timeout = timeout or _DEFAULT_TIMEOUT
        self.retry_count = _DEFAULT_RETRY_COUNT

    def search(self, query: str, num: int = 10, recency: str | None = None) -> list[dict]:
        import urllib.parse

        params = {"q": query, "count": num}
        url = f"{self.URL}?{urllib.parse.urlencode(params)}"
        payload = _http_request_with_retry(
            self.name, "GET", url,
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            body=None, timeout=self.timeout, retry_count=self.retry_count,
        )
        results = (payload.get("web") or {}).get("results", [])
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", ""), "date": r.get("age")}
            for r in results
        ]


PROVIDER_CLASSES = {
    "you": YouProvider,
    "tavily": TavilyProvider,
    "exa": ExaProvider,
    "brave": BraveProvider,
    "serper": SerperProvider,
}


def construct_provider(provider_name: str):
    """Constructs a real, live provider instance for `provider_name`.
    Raises RuntimeError (via that provider's own __init__) if its key
    is not configured -- never returns a provider that would silently
    fail. Callers (search_provider_manager.py) always check
    is_provider_configured() first regardless."""
    cls = PROVIDER_CLASSES.get(provider_name)
    if cls is None:
        raise ValueError(f"Unknown search provider: {provider_name!r}. Known: {sorted(PROVIDER_CLASSES)}")
    return cls()


# ---------------------------------------------------------------------
# Recording / replay (unchanged from Phase 14)
# ---------------------------------------------------------------------

class RecordingProvider:
    """Wraps a live provider and saves every response to `out_path` as
    it goes -- a real live run becomes a replay fixture for free. Used
    only during controlled live validation, never in the default/
    offline path."""

    def __init__(self, inner: SearchProvider, out_path: str):
        self.inner = inner
        self.out_path = out_path
        self.name = inner.name
        self.log: dict[str, list[dict]] = {}

    def search(self, query: str, num: int = 10, recency: str | None = "qdr:w") -> list[dict]:
        result = self.inner.search(query, num, recency)
        self.log[query] = result
        with open(self.out_path, "w", encoding="utf-8") as f:
            json.dump(self.log, f, indent=2)
        return result


class ReplayProvider:
    """Serves previously-recorded responses -- deterministic, zero API
    credits, safe for CI and offline tests. Never makes a network call."""

    name = "replay"

    def __init__(self, fixture_path: str | None = None, fixture_data: dict | None = None):
        if fixture_data is not None:
            self.data = fixture_data
        elif fixture_path is not None:
            with open(fixture_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            raise ValueError("ReplayProvider requires fixture_path or fixture_data")

    def search(self, query: str, num: int = 10, recency: str | None = None) -> list[dict]:
        return list(self.data.get(query, []))
