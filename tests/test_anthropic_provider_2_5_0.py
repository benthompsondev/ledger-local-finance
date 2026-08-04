"""Anthropic's Messages API, exercised over a real loopback socket.

Anthropic is not OpenAI-compatible, and 2.4.0 offered no way to say so: the
only route to it was "Somewhere else" with its base URL, which sent an
OpenAI chat request to a path that does not exist there. This module covers
the guided provider that replaces that.

What kind of test this is matters, so it is stated plainly:

  * these are **real loopback HTTP tests**. A genuine HTTPServer answers on
    127.0.0.1 and the provider code opens a real socket to it, so URL
    construction, headers, status handling and JSON decoding are all
    exercised for real;
  * they are **not** live external-provider tests. Nothing here contacts
    api.anthropic.com, and no Anthropic key exists in this repository. The
    one thing simulated is the host *detection*: `_ANTHROPIC_HOST` is
    pointed at the loopback address so the Anthropic branch is taken. The
    protocol itself is not simulated, it is the shipping code.

Whether the real service accepts these requests is therefore not proven
here. That needs a key and a live call, and is recorded as unverified.

Every figure is invented.
"""
from __future__ import annotations

import io
import json

import pytest

import utils.ai_assist as ai
from tests.test_ai_provider_matrix_2_4_0 import FakeProvider
from utils.ai_assist import (
    AiProviderError, explain_finding, save_settings,
)
from utils.ai_assist import test_connection as check_connection  # not a test

SECRET = "INVENTED_ANTHROPIC_TEST_KEY"


@pytest.fixture
def provider():
    fake = FakeProvider().start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def anthropic(monkeypatch, provider):
    """Route the loopback server through the Anthropic protocol branch."""
    monkeypatch.setattr(ai, "_ANTHROPIC_HOST", "127.0.0.1")
    return provider


def message(text="NORTHSTAR_CONNECTION_OK", *, stop_reason="end_turn",
            content=None) -> dict:
    """A reply shaped the way Anthropic shapes one."""
    return {
        "id": "msg_invented",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": content if content is not None else [
            {"type": "text", "text": text},
        ],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 11, "output_tokens": 22},
    }


def _configure(conn, provider, **extra):
    values = dict(enabled=True, base_url=provider.url,
                  model="claude-sonnet-4-5", scope="summary", api_key=SECRET)
    values.update(extra)
    return save_settings(conn=conn, **values)


def _finding() -> dict:
    return {
        "title": "Groceries rose",
        "detail": "Groceries were $420.00 last month, against a $360.00 usual.",
        "figures": {"amount": 420.00, "usual": 360.00},
    }


# ── the request ──────────────────────────────────────────────────────────

def test_the_request_goes_to_the_messages_endpoint(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.body = message()

    check_connection(conn=ledger_db)

    assert anthropic.requests[0]["path"] == "/v1/messages"


def test_the_key_travels_in_x_api_key_not_a_bearer(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.body = message()

    check_connection(conn=ledger_db)

    headers = {k.lower(): v for k, v in anthropic.requests[0]["headers"].items()}
    assert headers["x-api-key"] == SECRET
    assert "authorization" not in headers


def test_the_api_version_header_is_sent_and_pinned(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.body = message()

    check_connection(conn=ledger_db)

    headers = {k.lower(): v for k, v in anthropic.requests[0]["headers"].items()}
    assert headers["anthropic-version"] == "2023-06-01"


def test_the_system_prompt_is_hoisted_out_of_the_messages(
    ledger_db, anthropic,
):
    """Anthropic rejects a system role inside the message list."""
    _configure(ledger_db, anthropic)
    anthropic.then(
        (200, message("Fine."), None),
        (200, message("Groceries drift up when prices move."), None),
    )

    explain_finding(_finding(), conn=ledger_db)

    body = anthropic.requests[-1]["body"]
    assert body.get("system"), "the system prompt was dropped entirely"
    assert {m["role"] for m in body["messages"]} <= {"user", "assistant"}


def test_the_token_cap_is_sent(ledger_db, anthropic):
    """max_tokens is required by the Messages API, not optional."""
    _configure(ledger_db, anthropic)
    anthropic.body = message()

    check_connection(conn=ledger_db)

    assert int(anthropic.requests[0]["body"]["max_tokens"]) > 0


# ── the response ─────────────────────────────────────────────────────────

def test_a_content_block_reply_is_read(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.body = message("Grocery bills drift up when prices move.")

    answer = explain_finding(_finding(), conn=ledger_db)

    assert "Grocery bills drift up" in answer["text"]


def test_several_text_blocks_are_joined(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.body = message(content=[
        {"type": "text", "text": "Grocery bills drift up. "},
        {"type": "text", "text": "Worth one look this week."},
    ])

    answer = explain_finding(_finding(), conn=ledger_db)

    assert "Grocery bills drift up" in answer["text"]
    assert "Worth one look this week" in answer["text"]


def test_non_text_blocks_are_ignored(ledger_db, anthropic):
    """A thinking or tool block is not part of the answer."""
    _configure(ledger_db, anthropic)
    anthropic.body = message(content=[
        {"type": "thinking", "thinking": "INVENTED_INTERNAL_TRACE"},
        {"type": "text", "text": "Grocery bills drift up when prices move."},
    ])

    answer = explain_finding(_finding(), conn=ledger_db)

    assert "INVENTED_INTERNAL_TRACE" not in answer["text"]
    assert "Grocery bills drift up" in answer["text"]


@pytest.mark.parametrize("stop_reason,kind", [
    ("max_tokens", "truncated"),
    ("refusal", "safety"),
    ("tool_use", "incomplete"),
])
def test_stop_reasons_are_classified(
    ledger_db, anthropic, stop_reason, kind,
):
    _configure(ledger_db, anthropic)
    anthropic.body = message("Anything.", stop_reason=stop_reason)

    with pytest.raises(AiProviderError) as raised:
        check_connection(conn=ledger_db)

    assert raised.value.kind == kind


def test_an_empty_content_list_is_an_empty_answer(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.body = message(content=[])

    with pytest.raises(AiProviderError) as raised:
        check_connection(conn=ledger_db)

    assert raised.value.kind == "empty"


@pytest.mark.parametrize("body", [
    {"id": "msg_invented"},                       # no content at all
    {"content": "not a list"},                    # content of the wrong type
    {"content": [{"type": "text"}]},              # a block with no text
])
def test_malformed_replies_are_refused_not_guessed(
    ledger_db, anthropic, body,
):
    _configure(ledger_db, anthropic)
    anthropic.status, anthropic.body, anthropic.raw_body = 200, body, None

    with pytest.raises(AiProviderError) as raised:
        check_connection(conn=ledger_db)

    assert raised.value.kind in {"unreadable", "empty"}


def test_a_non_json_reply_is_refused(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.send_raw("<html>invented gateway page</html>")

    with pytest.raises(AiProviderError) as raised:
        check_connection(conn=ledger_db)

    assert raised.value.kind == "unreadable"


@pytest.mark.parametrize("status,kind", [
    (401, "authentication"),
    (403, "permission"),
    (429, "rate_limit"),
    (500, "transient"),
    (529, "transient"),
])
def test_http_failures_are_classified(ledger_db, anthropic, status, kind):
    _configure(ledger_db, anthropic)
    anthropic.fail(status, {"type": "error", "error": {
        "type": "invented_error", "message": "invented provider failure",
    }})

    with pytest.raises(AiProviderError) as raised:
        check_connection(conn=ledger_db)

    assert raised.value.kind == kind


# ── the protections that must survive a new provider ─────────────────────

def test_the_key_never_appears_in_an_error_message(ledger_db, anthropic):
    _configure(ledger_db, anthropic)
    anthropic.fail(401)

    with pytest.raises(AiProviderError) as raised:
        check_connection(conn=ledger_db)

    assert SECRET not in str(raised.value)


def test_a_provider_that_echoes_the_key_cannot_write_it_to_the_log(
    tmp_path, monkeypatch,
):
    """The same exposure the OpenAI path was hardened against in 2.4.0.

    Asserting on the raised error is not enough: the engine logs failures
    with logger.exception, which formats the whole chained traceback into
    native-engine.log.
    """
    import urllib.error

    import utils.database as db
    from desktop.engine import ledger_engine

    monkeypatch.setattr(ai, "_ANTHROPIC_HOST", "api.anthropic.com")
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()

    conn = db.get_connection()
    try:
        save_settings(conn=conn, enabled=True,
                      base_url="https://api.anthropic.com/v1",
                      model="claude-sonnet-4-5", api_key=SECRET)
    finally:
        conn.close()

    def _echo_the_key(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, f"Unauthorized {SECRET}", {}, None,
        )

    output = io.StringIO()
    with monkeypatch.context() as patched:
        patched.setattr("urllib.request.urlopen", _echo_the_key)
        ledger_engine.run(
            io.StringIO(json.dumps({
                "action": "test_ai_connection", "params": {},
            }) + "\n"),
            output,
        )

    # Prove the action ran. An unknown action name would fail this test the
    # cheerful way: nothing is sent, so nothing leaks, so it passes without
    # exercising a single line of the provider code.
    body = json.loads(output.getvalue())
    assert body["ok"] is False, body
    assert "unsupported" not in str(body.get("error", "")).lower(), body

    assert SECRET not in output.getvalue()
    log = tmp_path / "logs" / "native-engine.log"
    if log.exists():
        assert SECRET not in log.read_text(encoding="utf-8", errors="replace")


def test_anthropic_is_treated_as_remote_and_needs_a_key(ledger_db):
    """It is not exempt from the rules just because the header differs."""
    saved = save_settings(conn=ledger_db, enabled=True,
                          base_url="https://api.anthropic.com/v1",
                          model="claude-sonnet-4-5", api_key=SECRET)

    assert saved["is_local"] is False
    assert saved["key_required"] is True


def test_anthropic_over_plain_http_is_refused(ledger_db):
    """HTTPS enforcement is not bypassed by the new branch."""
    with pytest.raises(ValueError) as raised:
        save_settings(conn=ledger_db, enabled=True,
                      base_url="http://api.anthropic.com/v1",
                      model="claude-sonnet-4-5", api_key=SECRET)

    assert "https" in str(raised.value).lower()


def test_the_stored_key_is_not_readable_in_the_clear(ledger_db):
    """Key storage is unchanged by the new provider."""
    save_settings(conn=ledger_db, enabled=True,
                  base_url="https://api.anthropic.com/v1",
                  model="claude-sonnet-4-5", api_key=SECRET)
    row = ledger_db.execute(
        "SELECT value FROM app_settings WHERE key='ai_api_key'"
    ).fetchone()

    assert row is not None
    assert row["value"] != SECRET, "the key was stored verbatim"


def test_the_provider_is_offered_in_settings():
    """The guided entry, so nobody has to discover the protocol themselves."""
    entry = next(
        (p for p in ai.PROVIDERS if p["value"] == "anthropic"), None,
    )

    assert entry is not None
    assert entry["base_url"] == "https://api.anthropic.com/v1"
    note = entry["note"].lower()
    assert "console" in note
    assert "subscription" in note, (
        "the note must say a Claude subscription is not an API key"
    )


def test_openai_says_its_key_is_separately_billed():
    entry = next((p for p in ai.PROVIDERS if p["value"] == "openai"), None)

    assert entry is not None
    note = entry["note"].lower()
    assert "billing" in note or "billed" in note
    assert "subscription" in note


def test_grounding_still_removes_unsupported_figures(ledger_db, anthropic):
    """A new provider must not become a way around the figure check."""
    _configure(ledger_db, anthropic)
    anthropic.then(
        (200, message(
            "Groceries hit $1,247.00 this month, up 61% on your $9,999.99 norm."
        ), None),
        (200, message(
            "Grocery bills drift up when prices move. Worth one look this week."
        ), None),
    )

    answer = explain_finding(_finding(), conn=ledger_db)

    for invented in ("1,247.00", "9,999.99", "61%"):
        assert invented not in answer["text"], answer["text"]
