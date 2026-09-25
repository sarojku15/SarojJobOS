// Shared helpers for the SarojJobOS local MVP frontend.
//
// Candidate identity: this is a single-user LOCAL DEVELOPMENT tool.
// The "current candidate" is remembered in this browser's
// localStorage -- there is no login, session, or server-side auth of
// any kind. This is NOT production security (any caller can claim to
// be any candidate_id -- there is no proof of identity); it only
// prevents THIS browser from accidentally mixing up two candidates it
// created itself. What IS enforced server-side: every route keyed by
// a bare search_id/run_id (not already nested under a candidate_id
// path) now REQUIRES an explicit ?candidate_id= query param and
// verifies the looked-up search/run actually belongs to that
// candidate_id (search_store.get_saved_search_for_candidate() /
// get_run_status_for_candidate()) -- candidate A can no longer read
// candidate B's search/run/results/report merely by guessing or
// reusing a search_id/run_id string. A real internet-facing multi-user
// deployment would still need actual authentication (verifying WHO is
// making the request) on top of this -- this ownership check only
// verifies the record belongs to whatever candidate_id was supplied,
// not that the supplied candidate_id is really the caller.
//
// Phase 13 fix: localStorage previously being trusted WITHOUT
// server-side validation caused a stale/deleted candidate_id (e.g.
// after a dev-DB reset) to render a raw "Unknown candidate: ..." API
// error on some pages while other pages silently kept using the same
// invalid id. Every page must now resolve the active candidate via
// resolveActiveCandidate() below, which validates against the API and
// clears the stale id automatically, instead of trusting localStorage
// directly.

const CANDIDATE_KEY = "jobos_candidate_id";

function getCandidateId() {
  return localStorage.getItem(CANDIDATE_KEY);
}

function setCandidateId(id) {
  localStorage.setItem(CANDIDATE_KEY, id);
}

function clearCandidateId() {
  localStorage.removeItem(CANDIDATE_KEY);
}

/**
 * The one place every page should use to find out "who is the active
 * candidate right now." Validates the locally-remembered id against
 * the server (GET /api/candidates/{id}) rather than trusting
 * localStorage blindly -- a stale id (candidate deleted, dev DB reset,
 * etc.) is cleared automatically and null is returned, so callers can
 * show a proper onboarding/empty state instead of a raw API error.
 *
 * Never hardcodes any specific candidate id -- works identically for
 * any candidate this browser has ever created.
 */
async function resolveActiveCandidate() {
  const id = getCandidateId();
  if (!id) return null;

  try {
    await api(`/api/candidates/${id}`);
    return id;
  } catch (err) {
    if (err.status === 404) {
      clearCandidateId();
      return null;
    }
    // A non-404 failure (network error, 500, etc.) is not evidence the
    // candidate is invalid -- surface it as "unknown" for navigation
    // purposes without erasing a possibly-still-valid id.
    throw err;
  }
}

/** For pages that MUST have a valid candidate to render at all.
 * Redirects home if none exists/validates. Returns the id otherwise. */
async function requireActiveCandidateOrRedirect() {
  try {
    const id = await resolveActiveCandidate();
    if (!id) {
      window.location.href = "/";
      return null;
    }
    return id;
  } catch (err) {
    window.location.href = "/";
    return null;
  }
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, opts);
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    data = { raw: text };
  }
  if (!resp.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${resp.status})`;
    const error = new Error(typeof message === "string" ? message : JSON.stringify(message));
    error.status = resp.status;
    error.data = data;
    throw error;
  }
  return data;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

/** Turns a caught error (including our own api() errors) into a short,
 * human, non-technical message -- never a raw traceback/stack. */
function friendlyErrorMessage(err, fallback) {
  if (!err) return fallback || "Something went wrong.";
  if (err.status === 404) {
    return "Your profile could not be loaded. Please create or select a candidate.";
  }
  if (err.status === 409) {
    return err.message || "Your profile needs to be confirmed before running a new search.";
  }
  if (err.status === 422) {
    return "Some of the information provided isn't valid. Please check the form and try again.";
  }
  if (err.status >= 500) {
    return "The server ran into a problem. Please try again in a moment.";
  }
  return err.message || fallback || "Something went wrong.";
}

function showError(container, err) {
  container.innerHTML = "";
  const message = err instanceof Error ? friendlyErrorMessage(err) : String(err);
  container.appendChild(el(`<div class="banner banner-error">${escapeHtml(message)}</div>`));
}

function showSuccess(container, message) {
  container.innerHTML = "";
  container.appendChild(el(`<div class="banner banner-success">${escapeHtml(message)}</div>`));
}

function showInfo(container, message) {
  container.innerHTML = "";
  container.appendChild(el(`<div class="banner banner-info">${escapeHtml(message)}</div>`));
}

function showLoading(container, message) {
  container.innerHTML = "";
  container.appendChild(el(`<div class="loading-state">${escapeHtml(message || "Loading...")}</div>`));
}

function showEmpty(container, message) {
  container.innerHTML = "";
  container.appendChild(el(`<div class="empty-state">${escapeHtml(message)}</div>`));
}

/** One human-facing line per source, driven by final_status (Phase
 * 14.2) -- never the raw investigation `reason` text as the primary
 * label. A restricted board with a configured search provider reads
 * "Available via Search Provider — Tavily", never a stale "Automated
 * discovery not authorized" once a provider actually makes it usable. */
function sourceStatusLine(s) {
  switch (s.final_status) {
    case "ENABLED":
      return { available: true, title: s.source_name, detail: null };
    case "AVAILABLE_VIA_SEARCH_PROVIDER":
      return {
        available: true,
        title: s.source_name,
        detail: `Search Provider: ${s.search_provider_active_provider_display || s.search_provider || "configured"}`,
      };
    case "SEARCH_PROVIDER_NOT_CONFIGURED":
      return {
        available: false,
        title: s.source_name,
        detail: "Search provider not configured",
        hint: "Configure a search API to enable discovery.",
      };
    case "MANUAL_IMPORT_AVAILABLE":
      return { available: false, title: s.source_name, detail: "No automated discovery available yet" };
    case "ATS_FALLBACK":
      return { available: false, title: s.source_name, detail: "Employer/ATS fallback (no board configured yet)" };
    case "NOT_CONFIGURED":
      return { available: false, title: s.source_name, detail: "Not configured" };
    default:
      return { available: false, title: s.source_name, detail: "Not available" };
  }
}

/** Renders the standard "Available / Unavailable" compact source list
 * from GET /api/sources's response -- shared by dashboard.html and
 * search_new.html so the two never drift apart. `data` is the full
 * /api/sources JSON body ({sources, enabled, unavailable}).
 * `providerSummary` (optional) is GET /api/settings/search-providers's
 * {providers: [...]} body, used only to render the small "Search
 * Provider Status" line (configured-provider count + failover note);
 * omit it to render without that line. */
function renderSourceStatus(container, data, providerSummary) {
  const available = data.enabled || [];
  const unavailable = data.unavailable || [];

  const availableHtml = available.length
    ? available
        .map((s) => {
          const line = sourceStatusLine(s);
          return `<div class="source-row source-available">&#10003; ${escapeHtml(line.title)}${line.detail ? ` &mdash; ${escapeHtml(line.detail)}` : ""}</div>`;
        })
        .join("")
    : `<div class="hint">No sources are currently enabled.</div>`;

  let providerSummaryHtml = "";
  if (providerSummary && providerSummary.providers) {
    const configuredCount = providerSummary.providers.filter((p) => p.configured).length;
    providerSummaryHtml = `
      <div class="source-status-block">
        <div class="source-status-heading">Search Provider Status</div>
        <div class="hint">${configuredCount} provider${configuredCount === 1 ? "" : "s"} configured &middot; Automatic failover: ${configuredCount > 0 ? "ON" : "OFF"}</div>
        <button type="button" class="link-button" onclick="window.location.href='/settings/search-providers'">Manage search providers</button>
      </div>
    `;
  }

  const unavailableId = `unavailable-${Math.random().toString(36).slice(2)}`;
  const unavailableHtml = unavailable.length
    ? `
      <div class="source-unavailable-summary">${unavailable.length} unavailable / not configured</div>
      <div id="${unavailableId}" hidden>
        ${unavailable
          .map((s) => {
            const line = sourceStatusLine(s);
            return `
              <div class="source-row source-unavailable">
                <b>${escapeHtml(line.title)}</b> &mdash; ${escapeHtml(line.detail)}
                ${line.hint ? `<div class="hint">${escapeHtml(line.hint)}</div>` : ""}
                ${s.manual_import_available ? `<div class="hint"><a href="/import">Import a job manually</a></div>` : ""}
              </div>
            `;
          })
          .join("")}
      </div>
      <button type="button" class="link-button" data-toggle="${unavailableId}">View source details</button>
    `
    : "";

  container.innerHTML = `
    <div class="source-status-block">
      <div class="source-status-heading">Available</div>
      ${availableHtml}
    </div>
    ${providerSummaryHtml}
    <div class="source-status-block">
      <div class="source-status-heading">Unavailable / Not Configured</div>
      ${unavailableHtml}
    </div>
  `;

  const toggleBtn = container.querySelector("[data-toggle]");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const target = document.getElementById(toggleBtn.dataset.toggle);
      target.hidden = !target.hidden;
      toggleBtn.textContent = target.hidden ? "View source details" : "Hide source details";
    });
  }
}

function badgeHtml(value, extraClass, label) {
  if (!value) return "";
  return `<span class="badge ${escapeHtml(value)} ${extraClass || ""}">${escapeHtml(label || value)}</span>`;
}

// Shared across results.html and dashboard.html -- the human-facing
// labels for search_run_sources.status (Phase 1's per-source execution
// audit: NOT_ATTEMPTED/NOT_CONFIGURED/BLOCKED/FAILED/ZERO/SUCCESS, one
// place so the wording can never drift between the two pages.
const SOURCE_STATUS_LABELS = {
  SUCCESS: "Success", ZERO: "Zero results", FAILED: "Failed",
  BLOCKED: "Blocked", NOT_CONFIGURED: "Not configured", NOT_ATTEMPTED: "Not attempted",
};

function sourceAuditSummaryHtml(sources) {
  if (!sources || !sources.length) return "";
  return sources
    .slice()
    .sort((a, b) => (a.board || a.source).localeCompare(b.board || b.source))
    .map((s) => `<span class="source-audit-chip">${escapeHtml(s.board || s.source)}: ${badgeHtml(s.status, "", SOURCE_STATUS_LABELS[s.status])} ${s.raw_count ?? 0}</span>`)
    .join(" ");
}
