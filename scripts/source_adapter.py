#!/usr/bin/env python3

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class BlockReason(Enum):
    NONE = "NONE"
    LOGIN_WALL = "LOGIN_WALL"
    CAPTCHA = "CAPTCHA"
    VERIFICATION = "VERIFICATION"
    MAINTENANCE = "MAINTENANCE"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN_BLOCK = "UNKNOWN_BLOCK"
    NETWORK_ERROR = "NETWORK_ERROR"


class AdapterBlockedError(Exception):
    def __init__(self, source, reason, detail=""):
        self.source = source
        self.reason = reason
        self.detail = detail
        message = f"{source} blocked: {reason.value}"
        if detail:
            message += f" - {detail}"
        super().__init__(message)


class AdapterTimeoutError(Exception):
    def __init__(self, source, detail=""):
        self.source = source
        self.detail = detail
        message = f"{source} timed out"
        if detail:
            message += f" - {detail}"
        super().__init__(message)


class AdapterStatus(Enum):
    """
    Whether a REGISTERED adapter is actually live/functional, as
    distinct from BlockReason (which describes why a live health check
    or search failed server-side). AdapterStatus is a local, no-network,
    pre-flight fact about the adapter itself.

    ENABLED: the adapter is implemented and may be queried live
    (NaukriAdapter and MockJobSourceAdapter are ENABLED today).

    NOT_ENABLED: the adapter is registered (so the rest of the pipeline
    can reason about its existence/capabilities structurally) but has no
    working implementation yet -- health_check()/search() must raise
    AdapterNotEnabledError immediately, never a fake empty result.

    REQUIRES_AUTH / BLOCKED / UNSUPPORTED: reserved for a source that has
    been investigated and found to need credentials, be persistently
    blocked, or be architecturally unsupportable without violating the
    project's automation-ethics policy (config/site_adapters.json) --
    not used by any adapter yet; a future adapter sets one of these only
    after actually investigating that source, never speculatively.
    """

    ENABLED = "ENABLED"
    NOT_ENABLED = "NOT_ENABLED"
    REQUIRES_AUTH = "REQUIRES_AUTH"
    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


class AdapterCapability(Enum):
    """
    What an adapter actually supports, self-declared per source. See
    JobSourceAdapter.capabilities. Never assumed present by default --
    an adapter with no working implementation declares an empty set.
    """

    SEARCH = "SEARCH"
    DETAIL = "DETAIL"
    NATIVE_FRESHNESS = "NATIVE_FRESHNESS"
    PAGINATION = "PAGINATION"
    APPLICATION_URL = "APPLICATION_URL"
    COMPANY_METADATA = "COMPANY_METADATA"
    SALARY = "SALARY"
    REMOTE_FILTER = "REMOTE_FILTER"
    LOCATION_FILTER = "LOCATION_FILTER"


class AdapterNotEnabledError(Exception):
    """
    Raised by a registered-but-not-yet-implemented adapter's
    health_check()/search() -- the explicit, honest "this source cannot
    be used yet" signal, never a fake empty/successful result. Mirrors
    AdapterBlockedError/AdapterTimeoutError's shape.
    """

    def __init__(self, source, status, detail=""):
        self.source = source
        self.status = status
        self.detail = detail
        message = f"{source} not enabled: {status.value}"
        if detail:
            message += f" - {detail}"
        super().__init__(message)


@dataclass
class SearchQuery:
    role: str
    location: str
    experience_years: int | None = None
    exclude_keywords: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    # Discovery-level freshness constraint ("do not retrieve jobs older
    # than this many days"), enforced at the source/adapter level
    # wherever the source supports it -- see naukri_adapter.py. None
    # means unrestricted (the existing, pre-this-feature behavior).
    # Distinct from freshness.py's classify_freshness(), which remains
    # a post-retrieval ranking/reporting attribute only.
    max_job_age_days: int | None = None


@dataclass
class RawJob:
    source: str
    company: str
    title: str
    location: str = ""
    work_model: str = ""
    job_url: str = ""
    application_url: str = ""
    posted_date: str = ""
    jd_text: str = ""
    experience_required: str = ""
    mandatory_skills: list = field(default_factory=list)
    preferred_skills: list = field(default_factory=list)


@dataclass
class AdapterHealth:
    source: str
    reachable: bool
    block_reason: BlockReason = BlockReason.NONE
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    detail: str = ""


@dataclass
class SourceRunState:
    source: str
    health: AdapterHealth | None = None
    blocked: bool = False
    blocked_reason: BlockReason | None = None
    queries_attempted: int = 0
    queries_succeeded: int = 0
    queries_failed: int = 0
    jobs_found: int = 0
    # Set only when this source was skipped pre-flight because its
    # adapter's status is not ENABLED (see
    # source_registry.discover_from_sources()) -- distinct from
    # `blocked`, which means a live health_check()/search() call
    # actually ran and was refused by the source server-side.
    # not_enabled sources make ZERO network calls.
    not_enabled: bool = False
    adapter_status: "AdapterStatus | None" = None
    # Wall-clock bounds of THIS source's own execution within
    # discover_from_sources() (ISO 8601, UTC) -- None for a not_enabled
    # source (skipped before any timing would be meaningful). Added so
    # a per-source persistence layer (search_run_sources) can record a
    # real duration instead of approximating with the whole run's span,
    # which would be wrong whenever sources take different amounts of
    # time (the normal case).
    started_at: str | None = None
    completed_at: str | None = None
    # Optional, adapter-reported extra structured facts about its own
    # execution (e.g. ApnaAdapter's detail_fetch_attempted/succeeded/
    # failed counts) -- {} for any adapter that doesn't report any
    # (the overwhelming majority). Never invented by the registry
    # itself; only ever copied verbatim from an adapter instance's own
    # `last_search_details` attribute, when present, after search()
    # returns. Persisted as search_run_sources.details_json (see
    # search_worker.py) so the source-execution audit can show real,
    # source-specific detail beyond the common raw/eligible/displayed
    # counts every adapter already reports.
    extra_details: dict = field(default_factory=dict)


@dataclass
class SourceHealthRecord:
    """
    Unified, in-memory-only reporting shape for a source's health, for a
    future daily report/dashboard layer. NOT persisted to any database
    table in this phase (see this project's Phase 4 architecture report
    for why: no schema migration is warranted before a second live
    source exists to actually populate one).

    Every field defaults to None/0/empty -- nothing here is ever
    fabricated for a source that has not actually been live-tested.
    Callers populate only what they actually observed; a source with no
    recorded history is representable exactly as "no data," never as a
    plausible-looking guess.
    """

    source: str
    status: "AdapterStatus" = AdapterStatus.NOT_ENABLED
    last_check_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    error_type: str | None = None
    http_status: int | None = None
    classifier: str | None = None
    query_count: int = 0
    job_count: int = 0
    latency_seconds: float | None = None
    notes: str = ""


class JobSourceAdapter(ABC):
    name = "BASE"

    # See source_registry.discover_from_sources()'s docstring for the
    # exact semantics this flag controls. Default False: a failed
    # health_check() is a hard gate -- it unconditionally skips every
    # query for this source, exactly the pre-existing behavior every
    # adapter has always had. An adapter overrides this to True only
    # with its own documented, evidence-based justification (see
    # NaukriAdapter for the one existing case) -- it is never assumed
    # safe by default for a new or existing adapter.
    health_check_is_advisory = False

    # Default ENABLED preserves every existing adapter's behavior
    # unchanged (MockJobSourceAdapter and NaukriAdapter never set this
    # explicitly, and both remain ENABLED). A skeleton adapter for a
    # source with no working implementation yet overrides this to
    # AdapterStatus.NOT_ENABLED -- see source_registry.discover_from_
    # sources(), which checks this BEFORE calling health_check(), so a
    # NOT_ENABLED source makes zero network calls.
    status = AdapterStatus.ENABLED

    # What this adapter actually supports (see AdapterCapability).
    # Empty by default -- a subclass must explicitly declare each
    # capability it has a real, working implementation for. Never
    # assumed from `status == ENABLED` alone.
    capabilities: frozenset = frozenset()

    @abstractmethod
    def search(self, query: SearchQuery):
        raise NotImplementedError

    def health_check(self):
        return AdapterHealth(
            source=self.name,
            reachable=True,
            block_reason=BlockReason.NONE,
            detail="default health check - no live probe performed",
        )


class MockJobSourceAdapter(JobSourceAdapter):
    name = "MOCK"
    capabilities = frozenset({AdapterCapability.SEARCH})

    def search(self, query: SearchQuery):
        role = query.role
        location = query.location

        return [
            {
                "source": self.name,
                "company": "Mock Technology",
                "title": role,
                "location": location,
                "work_model": "Hybrid",
                "job_url": (
                    "https://example.com/jobs/"
                    + role.lower().replace(" ", "-").replace("/", "-")
                ),
                "application_url": (
                    "https://example.com/apply/"
                    + role.lower().replace(" ", "-").replace("/", "-")
                ),
                "posted_date": "2026-09-19",
                "jd_text": (
                    "Senior Site Reliability Engineer / DevOps role. "
                    "Requires AWS and Azure cloud, Kubernetes, "
                    "EKS and AKS, Terraform and Ansible, Jenkins, "
                    "GitHub Actions and ArgoCD CI/CD, Prometheus, "
                    "Grafana, Splunk and Dynatrace observability. "
                    "Responsibilities include SLI, SLO, SLA, error "
                    "budgets, incident management, RCA, postmortems, "
                    "MTTR improvement, reliability engineering, "
                    "production operations and automation. "
                    "Requires 10+ years of experience."
                ),
                "experience_required": "10+ years",
                "mandatory_skills": [
                    "AWS",
                    "Azure",
                    "Kubernetes",
                    "Terraform",
                    "Ansible",
                    "Jenkins",
                ],
                "preferred_skills": [
                    "GitHub Actions",
                    "ArgoCD",
                    "Prometheus",
                    "Grafana",
                    "Splunk",
                    "Dynatrace",
                    "SLI",
                    "SLO",
                    "SLA",
                    "Error Budgets",
                    "Incident Management",
                    "RCA",
                    "Postmortems",
                ],
            }
        ]


if __name__ == "__main__":
    adapter = MockJobSourceAdapter()

    print("Adapter health check")
    print("====================")
    print(adapter.health_check())

    print()
    print("Mock discovery")
    print("==============")

    jobs = adapter.search(
        SearchQuery(
            role="Senior Site Reliability Engineer",
            location="Bengaluru",
        )
    )

    print(f"Jobs discovered: {len(jobs)}")

    for job in jobs:
        print(
            f"{job['source']} | "
            f"{job['company']} | "
            f"{job['title']} | "
            f"{job['location']}"
        )
