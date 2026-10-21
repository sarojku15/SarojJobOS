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

// ===========================================================================
// Application shell (dark sidebar / mobile top bar + drawer)
// Single source of truth for nav destinations, icons, and labels --
// every page calls renderAppShell(key) once, into
// <div id="app-shell-root"></div>, instead of hand-writing a <header>.
// Destinations/hrefs are UNCHANGED from the project's original plain
// top-nav bar -- this only changes presentation, never routes.
// ===========================================================================

const NAV_ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /><path d="M9 21v-6h6v6" />',
  profile: '<circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8" />',
  searches: '<circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />',
  applications: '<rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><path d="M3 12h18" />',
  settings: '<circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />',
  scoring: '<line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" />',
};

const NAV_ITEMS = [
  { key: "home", href: "/", label: "Home", icon: "home" },
  { key: "profile", href: "/profile", label: "Profile", icon: "profile" },
  { key: "searches", href: "/searches", label: "Searches", icon: "searches" },
  { key: "dashboard", href: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { key: "applications", href: "/applications", label: "Applications", icon: "applications" },
  { key: "settings", href: "/settings/search-providers", label: "Settings", icon: "settings" },
  { key: "scoring-profiles", href: "/settings/scoring-profiles", label: "Scoring Profiles", icon: "scoring" },
];

function _navIconSvg(key) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NAV_ICONS[key] || ""}</svg>`;
}

function _navLinksHtml(activeKey) {
  return NAV_ITEMS
    .map((item) => {
      const isActive = item.key === activeKey;
      return `<a class="app-nav-link${isActive ? " active" : ""}" href="${item.href}"${isActive ? ' aria-current="page"' : ""}>${_navIconSvg(item.icon)}<span>${escapeHtml(item.label)}</span></a>`;
    })
    .join("");
}

/**
 * Renders the shared application shell (dark sidebar on desktop, a top
 * bar + slide-in drawer on narrow viewports) into
 * <div id="app-shell-root"></div>. `activeKey` is one of NAV_ITEMS'
 * `key` values, or null/omitted if no destination should be marked
 * active (preserves the handful of pages -- e.g. job workspace, manual
 * import -- that never highlighted a nav item under the old top bar
 * either). Call this once, as early as possible in each page's own
 * script, right after app.js loads.
 */
function renderAppShell(activeKey) {
  const root = document.getElementById("app-shell-root");
  if (!root) return;
  document.body.classList.add("has-app-shell");

  root.innerHTML = `
    <div class="app-drawer-overlay" id="app-drawer-overlay"></div>
    <header class="app-topbar">
      <span class="brand"><span class="brand-mark"></span>Job Search OS</span>
      <button type="button" class="app-menu-btn" id="app-menu-btn" aria-label="Open navigation menu" aria-expanded="false" aria-controls="app-sidebar">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
    </header>
    <nav class="app-sidebar" id="app-sidebar" aria-label="Main navigation">
      <div class="brand"><span class="brand-mark"></span>Job Search OS</div>
      ${_navLinksHtml(activeKey)}
      <div class="app-sidebar-footer">Local, single-candidate-per-browser</div>
    </nav>
  `;

  const sidebar = document.getElementById("app-sidebar");
  const overlay = document.getElementById("app-drawer-overlay");
  const menuBtn = document.getElementById("app-menu-btn");

  function closeDrawer() {
    sidebar.classList.remove("open");
    overlay.classList.remove("open");
    menuBtn.setAttribute("aria-expanded", "false");
  }
  function openDrawer() {
    sidebar.classList.add("open");
    overlay.classList.add("open");
    menuBtn.setAttribute("aria-expanded", "true");
  }

  menuBtn.addEventListener("click", () => {
    sidebar.classList.contains("open") ? closeDrawer() : openDrawer();
  });
  overlay.addEventListener("click", closeDrawer);
  sidebar.querySelectorAll("a").forEach((a) => a.addEventListener("click", closeDrawer));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
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
