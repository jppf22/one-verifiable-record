/* Reviewer Dashboard: Smart queue of claim links needing human review.
 * 
 * Features:
 * - Paginated list of links needing review
 * - Smart filtering: by status, language, reviewer
 * - Quick review actions inline
 * - Navigate to full comparison view
 * - Batch assignment
 * 
 * Route: #/review
 */
import { api } from "./api.js";
import { $, $$, el, esc, toast, getReviewer } from "./util.js";
import { emit, on } from "./bus.js";
import { pageHref } from "./router.js";
import * as review from "./review.js";

const LIMIT = 25;
const STATUS_OPTIONS = [
  { value: "all", label: "All Need Review" },
  { value: "contradicted", label: "Conflicts" },
  { value: "possible_conflict", label: "Possible Conflicts" },
  { value: "possible_match", label: "Possible Matches" },
  { value: "unverifiable", label: "Unverifiable" },
  { value: "supported", label: "Supported" },
  { value: "agree", label: "Agree" },
];

const LANG_OPTIONS = [
  { value: "", label: "All Languages" },
  { value: "fi", label: "Finnish" },
  { value: "sv", label: "Swedish" },
  { value: "en", label: "English" },
];

let state = {
  items: [],
  total: 0,
  limit: LIMIT,
  offset: 0,
  status: "all",
  lang: "",
  reviewer: "",
  loading: false,
  selected: new Set(),
};

const host = () => $("#review-dashboard");
const queueHost = () => $("#review-queue");
const paginationHost = () => $("#review-pagination");

/** Render the dashboard header with stats. */
function renderHeader() {
  const reviewer = getReviewer();
  const header = $("#review-header");
  if (!header) return;
  
  header.innerHTML = `
    <div class="dashboard-header">
      <div class="dashboard-title">
        <h2>Reviewer Dashboard</h2>
        <p class="muted">${state.total} claims need review${reviewer ? ` | Reviewing as: <b>${esc(reviewer)}</b>` : ''}</p>
      </div>
      <div class="dashboard-actions">
        <button type="button" class="primary" id="review-assign-selected" disabled>
          <span class="count">0</span> Assign Selected to Me
        </button>
        <button type="button" class="small" id="review-refresh">
          <span class="icon" aria-hidden="true">🔄</span> Refresh
        </button>
      </div>
    </div>
  `;
  
  // Update assign button
  updateAssignButton();
}

/** Render filter controls. */
function renderFilters() {
  const filters = $("#review-filters");
  if (!filters) return;
  
  const statusOptions = STATUS_OPTIONS.map((opt) =>
    `<option value="${esc(opt.value)}"${state.status === opt.value ? ' selected' : ''}>${esc(opt.label)}</option>`
  ).join("");
  
  const langOptions = LANG_OPTIONS.map((opt) =>
    `<option value="${esc(opt.value)}"${state.lang === opt.value ? ' selected' : ''}>${esc(opt.label)}</option>`
  ).join("");
  
  const reviewer = getReviewer();
  const myReviewsChecked = state.reviewer === reviewer ? ' checked' : '';
  
  filters.innerHTML = `
    <div class="review-filters">
      <div class="filter-group">
        <label for="review-status">Status:</label>
        <select id="review-status">${statusOptions}</select>
      </div>
      <div class="filter-group">
        <label for="review-lang">Language:</label>
        <select id="review-lang">${langOptions}</select>
      </div>
      <div class="filter-group checkbox">
        <label>
          <input type="checkbox" id="review-my-reviews"${myReviewsChecked}>
          Only my reviews
        </label>
      </div>
      <div class="filter-group">
        <label for="review-search">Search:</label>
        <input type="text" id="review-search" placeholder="Search claims..." value="${esc(state.search || '')}">
      </div>
      <button type="button" class="small" id="review-apply-filters">Apply</button>
      <button type="button" class="small muted" id="review-reset-filters">Reset</button>
    </div>
  `;
}

/** Render a single queue item. */
function renderItem(item) {
  const reviewer = getReviewer();
  const isSelected = state.selected.has(item.link_id);
  const hasReview = item.review_count > 0;
  const myReview = hasReview && state.reviewer === reviewer;
  
  // Determine verdict badge class
  const verdictClass = `badge ${item.verdict}`;
  
  // Truncate text for display
  const truncate = (text, len = 100) => {
    if (!text) return "";
    return text.length > len ? text.substring(0, len) + "…" : text;
  };
  
  // Get page link for navigation
  const pageLang = item.page_a.lang;
  const pageTitle = item.page_a.title;
  const pageUrl = pageHref(pageLang, pageTitle);
  
  // Check if this link has been reviewed by current user
  const reviewedByMe = item.has_review && reviewer && state.reviewer === reviewer;
  
  return el("div", {
    className: `queue-item${isSelected ? " selected" : ""}${reviewedByMe ? " reviewed-by-me" : ""}`,
    "data-link-id": item.link_id
  }, 
    el("div", { className: "queue-item-header" },
      el("input", {
        type: "checkbox",
        className: "select-checkbox",
        "data-link-id": item.link_id,
        checked: isSelected,
        onchange: () => toggleSelect(item.link_id)
      }),
      el("span", { className: verdictClass }, item.verdict),
      el("span", { className: "similarity" }, `Similarity: ${(item.similarity * 100).toFixed(0)}%`),
      el("span", { className: "review-count" }, 
        hasReview ? `👥 ${item.review_count} vote${item.review_count !== 1 ? 's' : ''}` : "No reviews yet"
      )
    ),
    el("div", { className: "queue-item-claims" },
      el("div", { className: "claim claim-a" },
        el("div", { className: "claim-meta" },
          el("span", { className: "lang-tag" }, item.claim_a.lang),
          el("span", { className: "page-link" }, 
            el("a", { href: pageUrl, onClick: (e) => { e.preventDefault(); navigateToPage(pageLang, pageTitle); } },
              `${esc(item.page_a.title)}`
            )
          ),
          el("span", { className: "predicate" }, item.claim_a.predicate ? `(${item.claim_a.predicate})` : "")
        ),
        el("div", { className: "claim-text" }, truncate(item.claim_a.text || item.claim_a.value)),
        el("div", { className: "claim-value" }, truncate(item.claim_a.value))
      ),
      el("div", { className: "claim claim-b" },
        el("div", { className: "claim-meta" },
          el("span", { className: "lang-tag" }, item.claim_b.lang),
          el("span", { className: "page-link" }, 
            el("a", { href: pageHref(item.page_b.lang, item.page_b.title), onClick: (e) => { e.preventDefault(); navigateToPage(item.page_b.lang, item.page_b.title); } },
              `${esc(item.page_b.title)}`
            )
          ),
          el("span", { className: "predicate" }, item.claim_b.predicate ? `(${item.claim_b.predicate})` : "")
        ),
        el("div", { className: "claim-text" }, truncate(item.claim_b.text || item.claim_b.value)),
        el("div", { className: "claim-value" }, truncate(item.claim_b.value))
      )
    ),
    el("div", { className: "queue-item-actions" },
      el("button", {
        type: "button",
        className: "action-btn review-btn",
        "data-link-id": item.link_id,
        title: "Quick review"
      }, "Quick Review"),
      el("button", {
        type: "button",
        className: "action-btn view-btn",
        "data-page-id": item.page_a.id,
        "data-page-lang": item.page_a.lang,
        "data-page-title": item.page_a.title,
        title: "View full comparison"
      }, "View Page"),
      reviewedByMe ? el("span", { className: "my-review-badge" }, "✓ My Review") : ""
    )
  );
}

/** Render the queue items. */
function renderQueue() {
  const container = queueHost();
  if (!container) return;
  
  if (state.loading) {
    container.innerHTML = `
      <div class="loading-state">
        <span class="spinner" aria-hidden="true"></span>
        <span>Loading claims needing review...</span>
      </div>
    `;
    return;
  }
  
  if (!state.items.length) {
    container.innerHTML = `
      <div class="empty-state">
        <p>No claims found matching your criteria.</p>
        <button type="button" class="small" id="review-reset-and-refresh">Reset filters and try again</button>
      </div>
    `;
    return;
  }
  
  const fragment = document.createDocumentFragment();
  state.items.forEach((item) => {
    fragment.appendChild(renderItem(item));
  });
  
  container.innerHTML = "";
  container.appendChild(fragment);
}

/** Render pagination controls. */
function renderPagination() {
  const container = paginationHost();
  if (!container) return;
  
  const totalPages = Math.ceil(state.total / state.limit);
  const currentPage = Math.floor(state.offset / state.limit) + 1;
  const hasPrev = state.offset > 0;
  const hasNext = state.offset + state.limit < state.total;
  
  container.innerHTML = `
    <div class="pagination">
      <button type="button" class="page-btn prev" ${!hasPrev ? 'disabled' : ''} data-page="prev">
        ← Previous
      </button>
      <span class="page-info">
        Page <b>${currentPage}</b> of <b>${totalPages}</b>
        (${state.total} total, showing ${state.offset + 1}-${Math.min(state.offset + state.limit, state.total)})
      </span>
      <button type="button" class="page-btn next" ${!hasNext ? 'disabled' : ''} data-page="next">
        Next →
      </button>
    </div>
  `;
}

/** Load queue items from API. */
async function loadQueue() {
  if (state.loading) return;
  
  state.loading = true;
  renderQueue();
  
  try {
    const params = {
      limit: state.limit,
      offset: state.offset,
    };
    if (state.status !== "all") params.status = state.status;
    if (state.lang) params.lang = state.lang;
    if (state.reviewer) params.reviewer = state.reviewer;
    if (state.search) {
      // Search is client-side filtered for now
      // Could be enhanced to server-side later
    }
    
    const data = await api.reviewQueue(params);
    state.items = data.items || [];
    state.total = data.total || 0;
    
    // Client-side search filter
    if (state.search) {
      const query = state.search.toLowerCase();
      state.items = state.items.filter((item) => {
        return (
          (item.claim_a.text || "").toLowerCase().includes(query) ||
          (item.claim_a.value || "").toLowerCase().includes(query) ||
          (item.claim_b.text || "").toLowerCase().includes(query) ||
          (item.claim_b.value || "").toLowerCase().includes(query) ||
          item.page_a.title.toLowerCase().includes(query) ||
          item.page_b.title.toLowerCase().includes(query) ||
          (item.claim_a.predicate || "").toLowerCase().includes(query) ||
          (item.claim_b.predicate || "").toLowerCase().includes(query)
        );
      });
      state.total = state.items.length; // Update total for client-side filtered results
    }
    
    renderQueue();
    renderPagination();
    renderHeader();
  } catch (err) {
    toast(`Failed to load review queue: ${err.message}`, "error");
    container.innerHTML = `
      <div class="error-state">
        <p>Error loading review queue: ${esc(err.message)}</p>
        <button type="button" class="small" id="review-retry">Retry</button>
      </div>
    `;
  } finally {
    state.loading = false;
  }
}

/** Toggle selection of a link. */
function toggleSelect(linkId) {
  if (state.selected.has(linkId)) {
    state.selected.delete(linkId);
  } else {
    state.selected.add(linkId);
  }
  updateAssignButton();
  renderQueue();
}

/** Select all items on current page. */
function selectAll() {
  state.items.forEach((item) => state.selected.add(item.link_id));
  updateAssignButton();
  renderQueue();
}

/** Deselect all items. */
function deselectAll() {
  state.selected.clear();
  updateAssignButton();
  renderQueue();
}

/** Update the assign button with current selection count. */
function updateAssignButton() {
  const btn = $("#review-assign-selected");
  if (!btn) return;
  const count = state.selected.size;
  btn.querySelector(".count").textContent = count;
  btn.disabled = count === 0;
}

/** Navigate to a page comparison view. */
function navigateToPage(lang, title) {
  window.location.hash = pageHref(lang, title);
  emit("route:navigate", { path: pageHref(lang, title) });
}

/** Apply filters and reload. */
function applyFilters() {
  const statusSelect = $("#review-status");
  const langSelect = $("#review-lang");
  const myReviewsCheckbox = $("#review-my-reviews");
  const searchInput = $("#review-search");
  
  if (statusSelect) state.status = statusSelect.value;
  if (langSelect) state.lang = langSelect.value;
  if (myReviewsCheckbox) {
    state.reviewer = myReviewsCheckbox.checked ? getReviewer() || "" : "";
  }
  if (searchInput) state.search = searchInput.value;
  
  state.offset = 0; // Reset to first page when filters change
  loadQueue();
}

/** Reset filters to defaults. */
function resetFilters() {
  state.status = "all";
  state.lang = "";
  state.reviewer = "";
  state.search = "";
  
  const statusSelect = $("#review-status");
  const langSelect = $("#review-lang");
  const myReviewsCheckbox = $("#review-my-reviews");
  const searchInput = $("#review-search");
  
  if (statusSelect) statusSelect.value = "all";
  if (langSelect) langSelect.value = "";
  if (myReviewsCheckbox) myReviewsCheckbox.checked = false;
  if (searchInput) searchInput.value = "";
  
  state.offset = 0;
  loadQueue();
}

/** Handle quick review from dashboard. */
async function quickReview(linkId, decision) {
  const result = await review.review(linkId, decision);
  if (result) {
    // Reload queue to reflect changes
    loadQueue();
    toast("Review submitted! Queue refreshed.", "ok", 3000);
  }
}

/** Initialize event listeners. */
function initEvents() {
  // Filter controls
  on("click", "#review-apply-filters", applyFilters);
  on("click", "#review-reset-filters", resetFilters);
  on("click", "#review-refresh", loadQueue);
  
  // Quick filter changes (auto-apply on change for some)
  const statusSelect = $("#review-status");
  const langSelect = $("#review-lang");
  const searchInput = $("#review-search");
  
  if (statusSelect) {
    statusSelect.addEventListener("change", () => { state.status = statusSelect.value; loadQueue(); });
  }
  if (langSelect) {
    langSelect.addEventListener("change", () => { state.lang = langSelect.value; loadQueue(); });
  }
  if (searchInput) {
    let debounceTimer;
    searchInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      state.search = e.target.value;
      debounceTimer = setTimeout(() => loadQueue(), 300);
    });
  }
  
  // Pagination
  on("click", "#review-pagination [data-page]", (e) => {
    const btn = e.target.closest("[data-page]");
    if (!btn) return;
    const action = btn.dataset.page;
    if (action === "prev" && state.offset > 0) {
      state.offset -= state.limit;
      loadQueue();
    } else if (action === "next" && state.offset + state.limit < state.total) {
      state.offset += state.limit;
      loadQueue();
    }
  });
  
  // Retry button for errors
  on("click", "#review-retry", loadQueue);
  on("click", "#review-reset-and-refresh", resetFilters);
  
  // Assign selected to me
  on("click", "#review-assign-selected", () => {
    const reviewer = getReviewer();
    if (!reviewer) {
      toast("Please set your reviewer name first", "warn");
      return;
    }
    if (state.selected.size === 0) return;
    
    // For now, just navigate to the first selected item
    // In future, this could batch assign
    const firstLinkId = [...state.selected][0];
    toast(`Assigned ${state.selected.size} items. Navigating to first item...`, "info");
    
    // Find the item and navigate to its page
    const item = state.items.find(i => i.link_id === firstLinkId);
    if (item) {
      navigateToPage(item.page_a.lang, item.page_a.title);
    }
  });
  
  // Queue item events (delegated)
  queueHost()?.addEventListener("click", (e) => {
    const reviewBtn = e.target.closest(".review-btn");
    const viewBtn = e.target.closest(".view-btn");
    const checkbox = e.target.closest(".select-checkbox");
    
    if (reviewBtn) {
      const linkId = Number(reviewBtn.dataset.linkId);
      // Open quick review dialog
      openQuickReview(linkId);
      return;
    }
    
    if (viewBtn) {
      const lang = viewBtn.dataset.pageLang;
      const title = viewBtn.dataset.pageTitle;
      if (lang && title) {
        navigateToPage(lang, title);
      }
      return;
    }
    
    if (checkbox) {
      const linkId = Number(checkbox.dataset.linkId);
      toggleSelect(linkId);
      return;
    }
  });
  
  // Keyboard shortcuts for dashboard
  document.addEventListener("keydown", (e) => {
    if (!host() || host().hidden) return;
    
    // Select all
    if (e.key === "a" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      selectAll();
    }
    
    // Deselect all
    if (e.key === "Escape") {
      deselectAll();
    }
  });
}

/** Open quick review dialog for a link. */
function openQuickReview(linkId) {
  const item = state.items.find(i => i.link_id === linkId);
  if (!item) return;
  
  const dialog = el("dialog", { className: "quick-review-dialog" }, 
    el("div", { className: "dialog-header" },
      el("h3", {}, "Quick Review"),
      el("button", { type: "button", className: "close-btn", onClick: () => dialog.close() }, "×")
    ),
    el("div", { className: "dialog-body" },
      el("p", {}, `Review link #${item.link_id}`),
      el("div", { className: "quick-review-claims" },
        el("div", { className: "claim" },
          el("strong", {}, `${item.page_a.lang}: `),
          el("span", {}, truncate(item.claim_a.text || item.claim_a.value, 80))
        ),
        el("div", { className: "claim" },
          el("strong", {}, `${item.page_b.lang}: `),
          el("span", {}, truncate(item.claim_b.text || item.claim_b.value, 80))
        )
      ),
      el("div", { className: "quick-review-verdict" },
        el("strong", {}, "Current verdict: "),
        el("span", { className: `badge ${item.verdict}` }, item.verdict)
      ),
      el("div", { className: "quick-review-actions" },
        el("p", {}, "Your vote:"),
        el("div", { className: "review-buttons" },
          el("button", {
            type: "button",
            className: "review-opt agree",
            onClick: async () => {
              await quickReview(linkId, "agree");
              dialog.close();
            }
          }, "✓ Agree"),
          el("button", {
            type: "button",
            className: "review-opt disagree",
            onClick: async () => {
              await quickReview(linkId, "disagree");
              dialog.close();
            }
          }, "✗ Disagree"),
          el("button", {
            type: "button",
            className: "review-opt fix",
            onClick: async () => {
              await quickReview(linkId, "fix");
              dialog.close();
            }
          }, "✎ Fix")
        )
      )
    )
  );
  
  document.body.appendChild(dialog);
  dialog.showModal();
  
  // Auto-focus first button
  const firstBtn = dialog.querySelector(".review-opt");
  if (firstBtn) firstBtn.focus();
  
  // Close on escape
  dialog.addEventListener("keydown", (e) => {
    if (e.key === "Escape") dialog.close();
  });
  
  // Clean up on close
  dialog.addEventListener("close", () => dialog.remove());
}

/** Helper to truncate text. */
function truncate(text, len) {
  if (!text) return "";
  return text.length > len ? text.substring(0, len) + "…" : text;
}

/** Show the dashboard. */
export function show() {
  if (!host()) return;
  host().hidden = false;
  renderHeader();
  renderFilters();
  renderQueue();
  renderPagination();
  loadQueue();
}

/** Hide the dashboard. */
export function hide() {
  const h = host();
  if (h) h.hidden = true;
}

/** Initialize the dashboard module. */
export function init() {
  initEvents();
  renderHeader();
  renderFilters();
}
