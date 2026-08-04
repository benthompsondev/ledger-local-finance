"""Provider interoperability matrix for the 2.4.0 hardening release.

Unlike the rest of the AI tests, which patch ``urllib.request.urlopen``, this
module stands up a real HTTP server on a loopback port and lets the provider
code make genuine socket requests through it. That exercises the parts a patch
cannot: URL construction, headers, status handling, timeouts and JSON decoding.

Nothing here needs an API key or the internet. Every figure is invented.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from utils.ai_assist import (
    AiProviderError, ask, explain_finding, get_settings, save_settings,
)
from utils.ai_assist import test_connection as check_connection  # not a test



# ─────────────────────────────────────────────────────────────────────
# A scriptable OpenAI-compatible server.
# ─────────────────────────────────────────────────────────────────────
class FakeProvider:
    """One loopback HTTP server whose next reply the test scripts."""

    def __init__(self):
        self.status = 200
        self.body = self._chat("NORTHSTAR_CONNECTION_OK")
        self.raw_body = None
        self.delay = 0.0
        self.requests: list[dict] = []
        self.script: list[tuple] = []
        self._server = None
        self._thread = None

    @staticmethod
    def _chat(content, *, finish_reason="stop", base_resp=None,
              choices=None) -> dict:
        payload = {
            "id": "invented-request-id",
            "choices": choices if choices is not None else [{
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }],
            "usage": {"total_tokens": 42},
        }
        if base_resp is not None:
            payload["base_resp"] = base_resp
        return payload

    def reply(self, content, **kwargs):
        self.status, self.raw_body = 200, None
        self.body = self._chat(content, **kwargs)

    def fail(self, status, body=None):
        self.status = status
        self.body = body if body is not None else {"error": {
            "message": "invented provider failure",
        }}
        self.raw_body = None

    def send_raw(self, text: str, status: int = 200):
        self.status, self.raw_body = status, text

    def then(self, *responses):
        """Queue a sequence of replies, one per request."""
        self.script = list(responses)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://127.0.0.1:{port}/v1"

    def start(self):
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = {"__unparsed__": raw}
                provider.requests.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": parsed,
                })
                if provider.script:
                    status, body, raw_body = provider.script.pop(0)
                else:
                    status, body, raw_body = (
                        provider.status, provider.body, provider.raw_body,
                    )
                if provider.delay:
                    time.sleep(provider.delay)
                data = (raw_body if raw_body is not None
                        else json.dumps(body)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def provider():
    fake = FakeProvider().start()
    try:
        yield fake
    finally:
        fake.stop()


def ok(content="NORTHSTAR_CONNECTION_OK", **kwargs):
    return (200, FakeProvider._chat(content, **kwargs), None)


def http(status, body=None):
    return (status, body or {"error": {"message": "invented failure"}}, None)


def _configure(conn, provider, *, model="invented-local-model", **extra):
    """Point the app at the fake server as a local OpenAI-compatible host."""
    values = dict(enabled=True, base_url=provider.url, model=model,
                  scope="summary")
    values.update(extra)
    return save_settings(conn=conn, **values)


# ─────────────────────────────────────────────────────────────────────
# The five supported configurations.
# ─────────────────────────────────────────────────────────────────────
LOCAL_CONFIGS = [
    ("ollama", "llama3.1", "http://127.0.0.1:11434/v1"),
    ("lm-studio", "invented-lmstudio-model", "http://localhost:1234/v1"),
]

REMOTE_CONFIGS = [
    ("minimax", "MiniMax-M3", "https://api.minimax.io/v1"),
    ("openai", "gpt-4o-mini", "https://api.openai.com/v1"),
    ("custom", "invented-model", "https://ai.invented.example/v1"),
]


@pytest.mark.parametrize("name,model,url", LOCAL_CONFIGS)
def test_local_endpoints_need_no_key_and_send_no_authorization(
    ledger_db, provider, name, model, url,
) -> None:
    """A loopback provider is trusted without a key, and none is sent."""
    saved = save_settings(conn=ledger_db, enabled=True,
                          base_url=provider.url, model=model)
    assert saved["is_local"] is True
    assert saved["key_required"] is False

    provider.reply("NORTHSTAR_CONNECTION_OK")
    result = check_connection(conn=ledger_db)

    assert result["ok"] is True
    assert result["model"] == model
    sent = provider.requests[-1]
    assert sent["path"].endswith("/chat/completions")
    assert "Authorization" not in sent["headers"]
    assert sent["body"]["model"] == model


@pytest.mark.parametrize("name,model,url", REMOTE_CONFIGS)
def test_remote_endpoints_require_https_and_send_the_key(
    ledger_db, name, model, url,
) -> None:
    """Remote providers must be HTTPS and must carry a bearer token."""
    saved = save_settings(conn=ledger_db, enabled=True, base_url=url,
                          model=model, api_key="invented-key-value")
    assert saved["is_local"] is False
    assert saved["key_required"] is True

    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def read(self):
            return json.dumps(
                FakeProvider._chat("NORTHSTAR_CONNECTION_OK")
            ).encode()

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode())
        return _Response()

    with patch("urllib.request.urlopen", _fake_urlopen):
        result = check_connection(conn=ledger_db)

    assert result["ok"] is True
    assert captured["url"] == url.rstrip("/") + "/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer invented-key-value"
    assert captured["body"]["model"] == model


def test_a_remote_provider_over_plain_http_is_refused_before_saving(
    ledger_db,
) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        save_settings(conn=ledger_db, enabled=True,
                      base_url="http://ai.invented.example/v1",
                      model="invented-model", api_key="invented-key")


# ─────────────────────────────────────────────────────────────────────
# Error matrix, driven through real sockets.
# ─────────────────────────────────────────────────────────────────────
ERRORS = [
    (400, "invalid_request"),
    (401, "authentication"),
    (403, "permission"),
    (404, "invalid_request"),
    (408, "invalid_request"),
    (429, "rate_limit"),
    (500, "transient"),
    (503, "transient"),
]


@pytest.mark.parametrize("status,kind", ERRORS)
def test_http_status_codes_map_to_one_classified_failure(
    ledger_db, provider, status, kind,
) -> None:
    _configure(ledger_db, provider)
    provider.fail(status)

    with pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert caught.value.kind == kind, f"{status} -> {caught.value.kind}"
    assert str(caught.value)
    assert "invented-key" not in str(caught.value)


MALFORMED = [
    ("not json at all", "unreadable"),
    ('{"choices": []}', "unreadable"),
    ('{"choices": [{"message": {}}]}', "unreadable"),
    ('{"choices": [{"message": {"content": ""}, '
     '"finish_reason": "stop"}]}', "empty"),
    ('["a", "list", "not", "an", "object"]', "unreadable"),
    ('{"unexpected": "shape"}', "unreadable"),
]


@pytest.mark.parametrize("raw,kind", MALFORMED)
def test_malformed_responses_never_reach_the_user(
    ledger_db, provider, raw, kind,
) -> None:
    _configure(ledger_db, provider)
    provider.send_raw(raw)

    with pytest.raises((AiProviderError, ValueError)) as caught:
        check_connection(conn=ledger_db)

    if isinstance(caught.value, AiProviderError):
        assert caught.value.kind == kind, f"{raw[:30]} -> {caught.value.kind}"


def test_a_truncated_answer_is_refused_not_shown(ledger_db, provider) -> None:
    _configure(ledger_db, provider)
    provider.then(
        ok("Half an ans", finish_reason="length"),
        ok("Half an ans", finish_reason="length"),
    )

    with pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert caught.value.kind == "truncated"


def test_a_safety_refusal_is_reported_honestly(ledger_db, provider) -> None:
    _configure(ledger_db, provider)
    provider.reply("", finish_reason="content_filter")

    with pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert caught.value.kind == "safety"


def test_connection_refused_is_a_local_model_hint(ledger_db, provider) -> None:
    """Point at a closed loopback port: the guidance must name the local app."""
    port = provider._server.server_address[1]
    provider.stop()
    save_settings(conn=ledger_db, enabled=True,
                  base_url=f"http://127.0.0.1:{port}/v1",
                  model="invented-local-model")

    with pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert caught.value.kind == "unreachable"
    assert "ollama" in str(caught.value).lower() or \
        "lm studio" in str(caught.value).lower()


# ─────────────────────────────────────────────────────────────────────
# Retry policy.
# ─────────────────────────────────────────────────────────────────────
def test_a_transient_failure_is_retried_once_and_can_succeed(
    ledger_db, provider,
) -> None:
    _configure(ledger_db, provider)
    provider.then(http(503), ok("NORTHSTAR_CONNECTION_OK"))

    result = check_connection(conn=ledger_db)

    assert result["ok"] is True
    assert len(provider.requests) == 2
    assert result["provider_attempts"] == 2


@pytest.mark.parametrize("status", [401, 403, 400])
def test_a_permanent_failure_is_not_retried(
    ledger_db, provider, status,
) -> None:
    """Retrying a rejected key or model just doubles the wait."""
    _configure(ledger_db, provider)
    provider.fail(status)

    with pytest.raises(AiProviderError):
        check_connection(conn=ledger_db)

    assert len(provider.requests) == 1, provider.requests


def test_minimax_reasoning_tags_are_not_shown_to_the_user(
    ledger_db, provider,
) -> None:
    _configure(ledger_db, provider)
    provider.reply(
        "<think>internal deliberation the user must never see</think>"
        "NORTHSTAR_CONNECTION_OK"
    )

    result = check_connection(conn=ledger_db)

    assert result["ok"] is True
    assert "internal deliberation" not in json.dumps(result)


# ─────────────────────────────────────────────────────────────────────
# Grounding and privacy.
# ─────────────────────────────────────────────────────────────────────
def test_the_connection_test_sends_no_financial_evidence(
    ledger_db, provider,
) -> None:
    from tests.conftest import seed_month

    seed_month(ledger_db, "2026-04")
    _configure(ledger_db, provider)
    provider.reply("NORTHSTAR_CONNECTION_OK")

    check_connection(conn=ledger_db)

    sent = json.dumps(provider.requests[-1]["body"]).lower()
    for leak in ("landlord", "payroll", "grocer", "1800", "2500", "hydro"):
        assert leak not in sent, f"connection test leaked {leak!r}"


def test_ai_disabled_makes_no_request_at_all(ledger_db, provider) -> None:
    _configure(ledger_db, provider)
    save_settings(conn=ledger_db, enabled=False)

    with pytest.raises(ValueError):
        ask("What should I do?", conn=ledger_db)

    assert provider.requests == []


def test_an_invented_figure_never_reaches_the_user(
    ledger_db, provider,
) -> None:
    """The model may explain the finding; it may not invent arithmetic."""
    _configure(ledger_db, provider)
    finding = {
        "title": "Groceries rose",
        "detail": "Groceries were $420.00 last month, against a $360.00 usual.",
        "figures": {"amount": 420.00, "usual": 360.00},
    }
    provider.then(
        ok("You actually spent $9,999.99 on groceries, a huge jump."),
        ok("Grocery bills drift up when prices move. Worth one check of the "
           "usual basket this week."),
    )

    answer = explain_finding(finding, conn=ledger_db)

    assert "9,999.99" not in answer["text"]
    assert "9999.99" not in answer["text"]


def test_a_figure_from_the_finding_is_not_falsely_rejected(
    ledger_db, provider,
) -> None:
    _configure(ledger_db, provider)
    finding = {
        "title": "Groceries rose",
        "detail": "Groceries were $420.00 last month, against a $360.00 usual.",
        "figures": {"amount": 420.00, "usual": 360.00},
    }
    provider.reply(
        "The $420.00 is above your usual $360.00 because prices moved. "
        "Worth one check of the basket this week."
    )

    answer = explain_finding(finding, conn=ledger_db)

    assert "420" in answer["text"], answer["text"]
    assert len(provider.requests) == 1, "a valid answer was needlessly retried"


def test_a_failed_question_leaves_the_coach_usable(
    ledger_db, provider,
) -> None:
    """A failure must not wedge Coach: the next question still works."""
    _configure(ledger_db, provider)
    provider.fail(500)

    with pytest.raises(AiProviderError):
        ask("Why did groceries rise?", conn=ledger_db)

    provider.script = []
    provider.reply("Grocery prices drift up. Check the basket this week.")
    recovered = ask("Why did groceries rise?", conn=ledger_db)

    assert recovered["answer"]


# ─────────────────────────────────────────────────────────────────────
# Settings under repeated use.
# ─────────────────────────────────────────────────────────────────────
def test_repeated_read_save_and_test_never_locks_the_database(
    ledger_db, provider,
) -> None:
    """Reproduction attempt for the intermittent 'database is locked'."""
    _configure(ledger_db, provider)
    provider.reply("NORTHSTAR_CONNECTION_OK")

    for index in range(25):
        settings = get_settings(conn=ledger_db)
        assert settings["enabled"] is True
        save_settings(conn=ledger_db, scope="summary",
                      months=(index % 12) + 1)
        result = check_connection(conn=ledger_db)
        assert result["ok"] is True

    assert len(provider.requests) == 25


def test_concurrent_settings_writes_do_not_lock_the_database(
    ledger_db, provider,
) -> None:
    """Several writers on their own connections, as separate requests are."""
    import utils.database as db

    _configure(ledger_db, provider)
    errors: list[Exception] = []

    def worker(index: int):
        try:
            conn = db.get_connection()
            try:
                for _ in range(10):
                    save_settings(conn=conn, months=(index % 12) + 1)
                    get_settings(conn=conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, [str(e) for e in errors]


def test_the_stored_key_is_never_returned_or_echoed_in_an_error(
    ledger_db, provider,
) -> None:
    save_settings(conn=ledger_db, enabled=True,
                  base_url="https://api.minimax.io/v1",
                  model="MiniMax-M3", api_key="invented-secret-key-123")

    settings = get_settings(conn=ledger_db)
    assert "invented-secret-key-123" not in json.dumps(settings)
    assert settings["api_key_set"] is True

    def _raise(request, timeout=None):
        import urllib.error
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized invented-secret-key-123",
            {}, None,
        )

    with patch("urllib.request.urlopen", _raise), \
            pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert "invented-secret-key-123" not in str(caught.value)


# ─────────────────────────────────────────────────────────────────────
# Failure modes raised by an external review of this matrix, verified
# here rather than taken on trust.
# ─────────────────────────────────────────────────────────────────────
def test_streamed_frames_for_an_unstreamed_request_are_refused(
    ledger_db, provider,
) -> None:
    """Several local runtimes emit SSE frames whatever the stream flag says.

    Each frame is valid JSON once the prefix is stripped, so the concern was
    that the body would slip past the non-JSON guard. It does not: the whole
    body is decoded at once, which fails cleanly.
    """
    _configure(ledger_db, provider)
    provider.send_raw(
        'data: {"choices":[{"delta":{"content":"Half"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" an answer"}}]}\n\n'
        'data: [DONE]\n\n'
    )

    with pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert caught.value.kind == "unreadable"


def test_a_null_choices_list_does_not_wedge_the_screen(
    ledger_db, provider,
) -> None:
    """`{"choices": null}` is a third state beyond missing and empty."""
    _configure(ledger_db, provider)
    provider.send_raw('{"choices": null}')

    with pytest.raises(AiProviderError) as caught:
        check_connection(conn=ledger_db)

    assert caught.value.kind == "unreadable"


def test_a_provider_that_accepts_the_body_then_stalls_times_out(
    ledger_db, provider,
) -> None:
    """Distinct from connection refused: the socket opens, then goes quiet."""
    _configure(ledger_db, provider)
    provider.delay = 6.0

    started = time.monotonic()
    with pytest.raises((AiProviderError, ValueError)):
        check_connection(conn=ledger_db, timeout=2)
    elapsed = time.monotonic() - started

    assert elapsed < 20, f"the request hung for {elapsed:.1f}s"


@pytest.mark.parametrize("text,description", [
    ("You could save $1.247 next month.", "locale decimal separator"),
    ("Try trimming about $1.2k from groceries.", "abbreviated thousands"),
    ("Aim for $9,999.99 of savings.", "plainly invented target"),
])
def test_figures_the_evidence_does_not_support_are_removed(
    ledger_db, provider, text, description,
) -> None:
    """Whatever notation the model reaches for, an unsupported figure goes."""
    _configure(ledger_db, provider)
    finding = {
        "title": "Groceries rose",
        "detail": "Groceries were $420.00 last month, against a $360.00 usual.",
        "figures": {"amount": 420.00, "usual": 360.00},
    }
    provider.then(ok(text), ok(
        "Grocery bills drift up when prices move. Worth one look at the "
        "usual basket this week."
    ))

    answer = explain_finding(finding, conn=ledger_db)

    for bad in ("1.247", "1.2k", "9,999.99"):
        assert bad not in answer["text"], f"{description}: {answer['text']}"


def test_a_provider_that_echoes_the_key_cannot_write_it_to_the_log(
    tmp_path, monkeypatch,
) -> None:
    """The sanitized message is not enough on its own.

    Asserting on the raised error misses the real exposure: the engine logs
    failures with logger.exception, which formats the whole chained
    traceback into native-engine.log. A provider that reflects the
    Authorization header back in its 401 reason would put the key there
    while the JSON returned to the screen stayed clean.
    """
    import io
    import json
    import urllib.error

    import utils.database as db
    from desktop.engine import ledger_engine

    secret = "invented-secret-key-123"
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()

    conn = db.get_connection()
    try:
        save_settings(conn=conn, enabled=True,
                      base_url="https://api.minimax.io/v1",
                      model="MiniMax-M3", api_key=secret)
    finally:
        conn.close()

    def _echo_the_key(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, f"Unauthorized {secret}", {}, None,
        )

    output = io.StringIO()
    with patch("urllib.request.urlopen", _echo_the_key):
        ledger_engine.run(io.StringIO(json.dumps({
            "action": "test_ai_connection", "params": {},
        }) + "\n"), output)

    assert secret not in output.getvalue()

    log_path = tmp_path / "logs" / "native-engine.log"
    assert log_path.exists(), "the engine did not write its log"
    assert secret not in log_path.read_text(encoding="utf-8"), (
        "the API key reached native-engine.log"
    )
