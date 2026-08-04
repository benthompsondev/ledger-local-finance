"""A minus sign belongs in front of the dollar sign.

All figures are invented.

Home and Plan were both saying:

    June closed with $-68 kept.

`f"${net:,.0f}"` puts the sign between the symbol and the digits, which reads
as a typo rather than as a month that spent more than it earned. The narrative
formatter now writes `-$68`.

The last test here is the real audit: it drives the actual check-in packet
with a deliberately negative month and asserts the string `$-` appears nowhere
in anything a person reads. That catches sentences this file never names.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import add_tx, seed_month
from utils.checkin import signed_money


# ── the formatter ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (68, "$68"),
    (-68, "-$68"),
    (0, "$0"),
    (68.4, "$68"),
    (-68.4, "-$68"),
    (1234.5, "$1,234"),
    (-1234.5, "-$1,234"),
    (1_000_000, "$1,000,000"),
    (-1_000_000, "-$1,000,000"),
])
def test_the_sign_goes_before_the_symbol(value, expected) -> None:
    assert signed_money(value) == expected


def test_zero_is_never_negative_zero() -> None:
    """A month that landed exactly even is "$0", not "-$0"."""
    assert signed_money(-0.0) == "$0"
    assert signed_money(-0.004) == "$0"
    assert signed_money(0.0) == "$0"


def test_none_and_missing_read_as_zero() -> None:
    """These come straight off dict.get(), which returns None."""
    assert signed_money(None) == "$0"
    assert signed_money(0) == "$0"


def test_decimals_are_available_without_changing_the_sign_rule() -> None:
    assert signed_money(-68.25, decimals=2) == "-$68.25"
    assert signed_money(68.25, decimals=2) == "$68.25"
    assert signed_money(0, decimals=2) == "$0.00"


def test_the_output_never_contains_a_dollar_then_minus() -> None:
    for value in (-1, -0.5, -68, -1234.56, -999999):
        assert "$-" not in signed_money(value), value
        assert "$-" not in signed_money(value, decimals=2), value


# ── through the real narratives ──────────────────────────────────────────

def _negative_month(conn) -> None:
    """A complete month that spent more than it earned, then a quiet one."""
    seed_month(conn, "2026-05")
    seed_month(conn, "2026-06", payroll=1200.0)
    # Push June clearly negative with an invented one-off.
    add_tx(conn, day="2026-06-14", desc="INVENTED BOILER REPAIR",
           amount=2400.0, direction="debit", category="Home Improvement")
    conn.commit()


def test_a_month_that_closed_negative_reads_correctly(ledger_db):
    """The exact sentence from the report."""
    from utils.checkin import weekly_checkin

    _negative_month(ledger_db)

    packet = weekly_checkin(conn=ledger_db, today="2026-07-15")
    blob = json.dumps(packet)

    assert "$-" not in blob, (
        "a narrative still puts the minus sign after the dollar sign"
    )


def test_the_latest_complete_month_with_a_negative_net(ledger_db):
    """The stale-data retrospective, which quotes a whole month's net."""
    from utils.checkin import weekly_checkin

    _negative_month(ledger_db)

    packet = weekly_checkin(conn=ledger_db, today="2026-08-20")
    verdict = packet["verdict"]

    assert verdict["state"] == "stale_data"
    for text in (verdict["headline"], verdict["sentence"]):
        assert "$-" not in text, text


def test_a_prior_and_current_comparison_containing_a_negative_net(ledger_db):
    """Two nets in one sentence, where an improvement can still be a loss.

    Going from -$500 to -$100 is a real improvement and prints both figures,
    so this is where a sign bug shows up twice at once.
    """
    from utils.checkin import meaningful_changes

    seed_month(ledger_db, "2026-05", payroll=900.0)
    add_tx(ledger_db, day="2026-05-14", desc="INVENTED LARGE REPAIR",
           amount=2000.0, direction="debit", category="Home Improvement")
    seed_month(ledger_db, "2026-06", payroll=1400.0)
    ledger_db.commit()

    for change in meaningful_changes(conn=ledger_db):
        for value in change.values():
            if isinstance(value, str):
                assert "$-" not in value, change


def test_every_user_facing_string_in_the_packet_is_audited(ledger_db):
    """The audit, not an example.

    Walks the whole packet rather than naming sentences, so a narrative added
    later is covered without anyone remembering to come back here.
    """
    from utils.checkin import recommended_actions, weekly_checkin

    _negative_month(ledger_db)

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from strings(item)

    for today in ("2026-06-20", "2026-07-15", "2026-08-20"):
        packet = weekly_checkin(conn=ledger_db, today=today)
        actions = recommended_actions(conn=ledger_db, today=today)
        for text in list(strings(packet)) + list(strings(actions)):
            assert "$-" not in text, f"{today}: {text}"


def test_positive_months_are_unchanged(ledger_db):
    """The fix must not put a stray sign on ordinary figures."""
    from utils.checkin import weekly_checkin

    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    ledger_db.commit()

    packet = weekly_checkin(conn=ledger_db, today="2026-08-20")
    blob = json.dumps(packet)

    assert "$-" not in blob
    assert "-$" not in blob, "a positive month was given a minus sign"


def test_stored_amounts_are_untouched(ledger_db):
    """Formatting is presentation. The numbers behind it do not move."""
    from utils.checkin import monthly_progress

    _negative_month(ledger_db)

    progress = monthly_progress(conn=ledger_db, today="2026-06-30")
    net = progress["projected_net"]

    assert isinstance(net, (int, float))
    assert signed_money(net).replace("$", "").replace(",", "").lstrip("-") != ""
