"""The generic OpenAI-compatible chat client (server/llm.py) against a local
mock server: auth header, extra-body merge, 429 retry, response_format 400
fallback, lenient JSON parsing, and the unconfigured path."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import llm
from config import Settings, settings


class MockHandler(BaseHTTPRequestHandler):
    mode = "ok"
    count = 0
    last = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).count += 1
        type(self).last = {"headers": dict(self.headers), "body": body, "count": type(self).count}
        if self.mode == "429thenok" and self.count == 1:
            self._reply(429, {"error": "rate limited"}, {"retry-after": "1"})
            return
        if self.mode == "badformat" and "response_format" in body:
            self._reply(400, {"error": {"message": "response_format unsupported"}})
            return
        content = '{"verdict": "supported", "confidence": 0.9}'
        if "response_format" not in body:
            content = 'Sure! {"verdict": "contradicted", "confidence": 0.4} done'
        self._reply(200, {"choices": [{"message": {"content": content}}]})

    def _reply(self, status, payload, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def client(server, monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(settings, "llm_api_key", "secret123")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_extra_body", {"thinking": {"type": "disabled"}})
    monkeypatch.setattr(settings, "llm_reasoning_effort", "")
    monkeypatch.setattr(settings, "llm_min_interval_s", 0.0)
    monkeypatch.setattr(settings, "llm_max_attempts", 5)
    monkeypatch.setattr(settings, "llm_timeout_s", 10.0)
    monkeypatch.setattr(settings, "llm_budget_s", 30.0)
    llm._client = None
    llm._next_start = 0.0
    llm._blocked_until = 0.0
    MockHandler.mode, MockHandler.count, MockHandler.last = "ok", 0, None
    return settings


def test_chat_json_ok(client):
    assert llm.configured()
    out = llm.chat_json("system prompt", "user prompt")
    assert out == {"verdict": "supported", "confidence": 0.9}
    last = MockHandler.last
    assert last["headers"].get("Authorization") == "Bearer secret123"
    assert last["body"]["model"] == "test-model"
    assert last["body"]["response_format"] == {"type": "json_object"}
    assert last["body"]["thinking"] == {"type": "disabled"}  # LLM_EXTRA_BODY merged
    assert last["body"]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]


def test_chat_text(client):
    assert "verdict" in llm.chat_text("s", "u")


def test_429_retry_after(client):
    MockHandler.mode = "429thenok"
    t0 = time.time()
    out = llm.chat_json("s", "u")
    assert out["verdict"] == "supported"
    assert time.time() - t0 >= 1.0  # waited for retry-after
    assert MockHandler.count == 2


def test_response_format_400_fallback(client):
    MockHandler.mode = "badformat"
    out = llm.chat_json("s", "u")
    assert out == {"verdict": "contradicted", "confidence": 0.4}  # lenient parse of preamble
    assert "response_format" not in MockHandler.last["body"]


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    assert not llm.configured()
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_json("s", "u")


def test_no_api_key_sends_no_auth_header(server, monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(settings, "llm_api_key", "")  # local servers need no key
    monkeypatch.setattr(settings, "llm_model", "m")
    monkeypatch.setattr(settings, "llm_extra_body", {})
    monkeypatch.setattr(settings, "llm_min_interval_s", 0.0)
    monkeypatch.setattr(settings, "llm_max_attempts", 3)
    monkeypatch.setattr(settings, "llm_timeout_s", 10.0)
    monkeypatch.setattr(settings, "llm_budget_s", 30.0)
    llm._client = None
    llm._next_start = 0.0
    llm._blocked_until = 0.0
    MockHandler.mode, MockHandler.count, MockHandler.last = "ok", 0, None
    llm.chat_json("s", "u")
    assert "Authorization" not in MockHandler.last["headers"]
