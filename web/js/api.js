/* Fetch wrappers for the page-centric (v2) endpoints in docs/page-contract.md. All requests go via /api (nginx proxy). */
const API = "/api";
const enc = encodeURIComponent;

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(`${API}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(0, "API unreachable. Is the server running?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON error */ }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export const api = {
  /** Title search: [{page, counterparts, conflicts}]. Only pages with >= 1 counterpart. */
  searchPages: (q, lang) => request(`/pages?q=${enc(q)}${lang ? `&lang=${enc(lang)}` : ""}`),
  /** Landing list: [{page, counterparts, conflicts, missing_here, only_here, agree}]. */
  conflictedPages: (limit = 50) => request(`/pages/conflicted?limit=${enc(limit)}`),
  /** {page, counterparts} or a 404 ApiError. */
  pageByTitle: (lang, title) => request(`/pages/by-title?lang=${enc(lang)}&title=${enc(title)}`),
  page: (id) => request(`/pages/${enc(id)}`),
  align: (pageId) => request(`/pages/${enc(pageId)}/align`, { method: "POST" }),
  compare: (pageId) => request(`/pages/${enc(pageId)}/compare`),
  recheck: (pageId, focusLinkIds = []) =>
    request(`/pages/${enc(pageId)}/recheck`, { method: "POST", body: { focus_link_ids: focusLinkIds } }),
  claimHistory: (claimId) => request(`/claims/${enc(claimId)}/history`),
  reviews: (linkId) => request(`/reviews?link_id=${enc(linkId)}`),
  submitReview: ({ claim_link_id, reviewer, decision, note }) =>
    request(`/reviews`, { method: "POST", body: { claim_link_id, reviewer, decision, note } }),
  suggestions: (pageId) => request(`/pages/${enc(pageId)}/suggestions`),
  exportSuggestion: (id) => request(`/suggestions/${enc(id)}/export`, { method: "POST" }),
  /** Grounded Q&A over this page and its counterpart pages. */
  qa: (pageId, q, lang) =>
    request(`/qa?page=${enc(pageId)}&q=${enc(q)}${lang ? `&lang=${enc(lang)}` : ""}`),
};
