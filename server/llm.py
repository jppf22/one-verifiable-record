"""Shared client for the Verda Inference chat API (OpenAI-compatible).

Measured limits (2026-11-14): 40 requests per minute for the whole account
(every process using the key shares it), signalled by HTTP 429 with a
``retry-after`` header (seconds) and ``x-ratelimit-remaining-requests`` /
``x-ratelimit-reset-requests`` on every response. A 429 comes back in ~30 ms,
so retrying in a tight loop burns the whole minute. Before this module the
server called the API once per claim pair with no pacing, so on a page with
400 candidate pairs most calls failed and the error text was stored as a
verdict.

This client:
- spaces call starts at least ``llm_min_interval_s`` apart (process-wide lock);
- sleeps until the window resets when the server says 0 requests remain;
- on 429 waits ``retry-after`` (+ jitter) and retries; on 5xx / timeout backs
  off 2, 4, 8 s;
- gives up when the next wait would exceed the caller's time budget, raising
  ``LLMUnavailable``. Callers then leave the work undone instead of inventing
  a result.
- disables the model's reasoning mode: with it on, batched calls spend their
  whole token budget thinking and return empty content.
"""
import json
import random
import re
import threading
import time

import httpx

from config import settings


class LLMUnavailable(Exception):
    """No usable answer: no key, budget exhausted, or repeated failures."""


_lock = threading.Lock()
_next_start = 0.0      # monotonic time before which no new call may start
_blocked_until = 0.0   # set from retry-after / ratelimit-reset headers
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    """Shared keep-alive client so batches reuse one TLS session to Verda."""
    global _client
    with _lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(
                timeout=settings.llm_timeout_s,
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
            )
        return _client


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())


def _seconds(value) -> float | None:
    """'38', '38s', '1m2s', '500ms' -> seconds."""
    if value is None:
        return None
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|s|m|h)", s):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


def _reserve_slot(deadline: float) -> None:
    """Block until this caller may start a request, or raise if past deadline."""
    global _next_start
    with _lock:
        now = time.monotonic()
        start = max(now, _next_start, _blocked_until)
        if start > deadline:
            raise LLMUnavailable("LLM rate limit: no request slot within the time budget")
        _next_start = start + settings.llm_min_interval_s
    if start > now:
        time.sleep(start - now)


def _block_for(seconds: float) -> None:
    global _blocked_until
    with _lock:
        _blocked_until = max(_blocked_until, time.monotonic() + seconds)


def chat_json(system: str, user: str, *, max_tokens: int = 600,
              budget_s: float | None = None) -> dict:
    """One JSON-mode chat completion. Returns the parsed JSON object."""
    if not settings.verda_api_key:
        raise LLMUnavailable("no LLM configured (set VERDA_API_KEY)")
    budget = settings.llm_budget_s if budget_s is None else budget_s
    deadline = time.monotonic() + budget
    body = {
        "model": settings.llm_model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    backoff = 2.0
    last_error = "unknown error"
    for _attempt in range(settings.llm_max_attempts):
        _reserve_slot(deadline)
        try:
            r = _http().post(
                f"{settings.verda_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.verda_api_key}"},
                json=body, timeout=settings.llm_timeout_s,
            )
        except httpx.HTTPError as exc:  # timeout, connection reset, ...
            last_error = f"{type(exc).__name__}"
            _block_for(backoff)
            backoff *= 2
            continue
        rem_raw = r.headers.get("x-ratelimit-remaining-requests")
        if rem_raw == "0":
            _block_for(_seconds(r.headers.get("x-ratelimit-reset-requests")) or 60)
        elif rem_raw in ("1", "2"):
            # Proactively cushion the last two slots of the window to avoid 429s.
            _block_for(3.0)
        if r.status_code == 429:
            wait = (_seconds(r.headers.get("retry-after"))
                    or _seconds(r.headers.get("x-ratelimit-reset-requests")) or 30)
            last_error = f"429 rate limited (retry after {wait:.0f}s)"
            _block_for(wait + random.uniform(0.3, 1.5))
            continue
        if r.status_code >= 500:
            last_error = f"HTTP {r.status_code}"
            _block_for(backoff)
            backoff *= 2
            continue
        if r.status_code >= 400:
            raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            content = _strip_fences(r.json()["choices"][0]["message"]["content"] or "")
            return json.loads(content)
        except (KeyError, IndexError, ValueError) as exc:
            last_error = f"unparseable reply ({type(exc).__name__})"
            continue
    raise LLMUnavailable(f"LLM unavailable after retries: {last_error}")
