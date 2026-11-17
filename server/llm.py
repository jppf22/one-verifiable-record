"""Shared client for any OpenAI-compatible chat-completions endpoint.

The same code talks to a locally deployed model (Ollama ``/v1``, vLLM,
LM Studio, llama.cpp, text-generation-inference, ...) and to a hosted API key
(OpenAI, OpenRouter, Together, Groq, Mistral, DeepSeek, ...): set
``LLM_BASE_URL`` (plus ``LLM_API_KEY`` when the endpoint requires one) and
``LLM_MODEL``; provider-specific request parameters go in ``LLM_EXTRA_BODY``
(JSON, merged into every request body).

This client:
- spaces call starts at least ``llm_min_interval_s`` apart (process-wide lock;
  0 by default — set it for rate-limited hosted APIs);
- honours ``retry-after`` / ``x-ratelimit-*`` headers when a server sends them;
- on 429 waits ``retry-after`` (+ jitter) and retries; on 5xx / timeout backs
  off 2, 4, 8 s; a 400 mentioning ``response_format`` retries once without the
  JSON-mode parameter (some local servers reject it);
- gives up when the next wait would exceed the caller's time budget, raising
  ``LLMUnavailable``. Callers then leave the work undone instead of inventing
  a result.
"""
import json
import random
import re
import threading
import time

import httpx

from config import settings


class LLMUnavailable(Exception):
    """No usable answer: not configured, budget exhausted, or repeated failures."""


_lock = threading.Lock()
_next_start = 0.0      # monotonic time before which no new call may start
_blocked_until = 0.0   # set from retry-after / ratelimit-reset headers
_client: httpx.Client | None = None


def configured() -> bool:
    """True when an LLM endpoint and model are configured."""
    return bool(settings.llm_base_url and settings.llm_model)


def _http() -> httpx.Client:
    """Shared keep-alive client so batches reuse one TLS session."""
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


def _parse_json_lenient(content: str) -> dict:
    """Parse the first JSON object in the reply; tolerate fences and preamble."""
    content = _strip_fences(content)
    try:
        data = json.loads(content)
    except ValueError:
        start = content.find("{")
        if start < 0:
            raise
        data, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


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


def _chat(system: str, user: str, *, max_tokens: int, budget_s: float | None,
          json_mode: bool) -> str:
    """One paced chat completion; returns the raw message content."""
    if not configured():
        raise LLMUnavailable("no LLM configured (set LLM_BASE_URL and LLM_MODEL)")
    budget = settings.llm_budget_s if budget_s is None else budget_s
    deadline = time.monotonic() + budget
    body = {
        "model": settings.llm_model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if settings.llm_reasoning_effort:
        body["reasoning_effort"] = settings.llm_reasoning_effort
    body.update(settings.llm_extra_body or {})
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    backoff = 2.0
    last_error = "unknown error"
    for _attempt in range(settings.llm_max_attempts):
        _reserve_slot(deadline)
        try:
            r = _http().post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers=headers, json=body, timeout=settings.llm_timeout_s,
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
        if r.status_code == 400 and "response_format" in body:
            # Some OpenAI-compatible servers reject the JSON-mode parameter;
            # retry once without it and parse the JSON out of the text.
            body = {k: v for k, v in body.items() if k != "response_format"}
            last_error = f"HTTP 400 (retrying without response_format): {r.text[:200]}"
            continue
        if r.status_code >= 400:
            raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            last_error = f"malformed response ({type(exc).__name__})"
            continue
    raise LLMUnavailable(f"LLM unavailable after retries: {last_error}")


def chat_json(system: str, user: str, *, max_tokens: int = 600,
              budget_s: float | None = None) -> dict:
    """One JSON-mode chat completion. Returns the parsed JSON object."""
    content = _chat(system, user, max_tokens=max_tokens, budget_s=budget_s, json_mode=True)
    try:
        return _parse_json_lenient(content)
    except ValueError as exc:
        raise LLMUnavailable(f"unparseable JSON reply: {exc}") from exc


def chat_text(system: str, user: str, *, max_tokens: int = 1024,
              budget_s: float | None = None) -> str:
    """One chat completion returning the raw text content."""
    return _chat(system, user, max_tokens=max_tokens, budget_s=budget_s, json_mode=False)
