/* Fetch wrappers for every endpoint in docs/frontend-contract.md. All requests go via /api (nginx proxy). */
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
  searchArticles: (q) => request(`/articles?q=${enc(q)}`),
  align: (qid) => request(`/align/${enc(qid)}`, { method: "POST" }),
  merge: (qid) => request(`/merge/${enc(qid)}`),
  recheck: (qid, focusLinkIds = []) =>
    request(`/recheck/${enc(qid)}`, { method: "POST", body: { focus_link_ids: focusLinkIds } }),
  claimHistory: (claimId) => request(`/claims/${enc(claimId)}/history`),
  reviews: (linkId) => request(`/reviews?link_id=${enc(linkId)}`),
  submitReview: ({ claim_link_id, reviewer, decision, note }) =>
    request(`/reviews`, { method: "POST", body: { claim_link_id, reviewer, decision, note } }),
  suggestions: (qid) => request(`/suggestions/${enc(qid)}`),
  exportSuggestion: (id) => request(`/suggestions/${enc(id)}/export`, { method: "POST" }),
  qa: (qid, q, lang) =>
    request(`/qa?qid=${enc(qid)}&q=${enc(q)}${lang ? `&lang=${enc(lang)}` : ""}`),
};
