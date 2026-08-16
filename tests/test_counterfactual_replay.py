"""Counterfactual replay — financial behaviour.

All transactions are invented. These assert what a replay must be true about
money: that its "actual" is the same actual the rest of the app reports, that
a cut frees exactly what was cut, and that it refuses to model anything the
imported data cannot support.
"""
from __future__ import annotations

import pytest

from tests.conftest import add_tx, seed_month


def _replay(conn, **kwargs):
    from utils.counterfactual import counterfactual_packet

    return counterfactual_packet(conn=conn, **kwargs)


# ── the anti-drift guarantee ──────────────────────────────────────────────

def test_actual_totals_match_monthly_aggregates_exactly(ledger_db):
    """A replay's baseline must be the same number Insights already shows.

    If these ever diverge the feature is lying about the month the user can
    see elsewhere in the app, which matters more than the replay itself.
    """
    from utils.insights import monthly_aggregates

    seed_month(ledger_db, "2026-03")
    seed_month(ledger_db, "2026-04")

    packet = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Groceries", reduction_pct=100)
    assert packet["available"], packet["reason"]

    aggregate = next(
        row for row in monthly_aggregates(conn=ledger_db)
        if row["month"] == "2026-04"
    )
    actual = packet["replay"]["actual"]
    assert actual["income"] == pytest.approx(aggregate["income"])
    assert actual["spending"] == pytest.approx(aggregate["spending"])
    assert actual["net"] == pytest.approx(aggregate["net"])
    assert actual["savings_rate"] == pytest.approx(aggregate["savings_rate"])


# ── what a cut is worth ───────────────────────────────────────────────────

def test_removing_a_category_frees_exactly_that_category(ledger_db):
    seed_month(ledger_db, "2026-04", grocery_days=10, grocery_amount=25)

    packet = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Groceries", reduction_pct=100)
    replay = packet["replay"]

    assert replay["available"]
    assert replay["matched_count"] == 10
    # 10 × $25 of groceries and nothing else.
    assert replay["freed"] == pytest.approx(250.0)
    assert replay["net_delta"] == pytest.approx(250.0)
    assert replay["counterfactual"]["spending"] == pytest.approx(
        replay["actual"]["spending"] - 250.0
    )
    assert replay["counterfactual"]["net"] == pytest.approx(
        replay["actual"]["net"] + 250.0
    )
    # Income is untouched by a spending change.
    assert replay["counterfactual"]["income"] == pytest.approx(
        replay["actual"]["income"]
    )


def test_partial_reduction_scales_proportionally(ledger_db):
    seed_month(ledger_db, "2026-04", grocery_days=10, grocery_amount=25)

    half = _replay(ledger_db, month="2026-04", target_kind="category",
                   target_key="Groceries", reduction_pct=50)["replay"]
    quarter = _replay(ledger_db, month="2026-04", target_kind="category",
                      target_key="Groceries", reduction_pct=25)["replay"]

    assert half["freed"] == pytest.approx(125.0)
    assert quarter["freed"] == pytest.approx(62.5)
    # Every matching row is still counted, just at a smaller amount.
    assert half["matched_count"] == 10
    assert half["removed_entirely"] is False


def test_a_refund_sharing_the_category_is_not_removed(ledger_db):
    """Cutting spending must not delete Money In that wore the same label.

    Treating the refund as part of the cut would credit the user twice: once
    for the spending removed and again for the income removed alongside it.
    """
    seed_month(ledger_db, "2026-04", grocery_days=10, grocery_amount=25)
    add_tx(ledger_db, day="2026-04-14", desc="GROCER REFUND", amount=40,
           direction="credit", category="Groceries")
    ledger_db.commit()

    replay = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Groceries", reduction_pct=100)["replay"]

    # Only the 10 debits matched; the refund is not one of them.
    assert replay["matched_count"] == 10
    assert replay["freed"] == pytest.approx(250.0)
    assert replay["counterfactual"]["income"] == pytest.approx(
        replay["actual"]["income"]
    )


def test_merchant_target_matches_only_that_merchant(ledger_db):
    seed_month(ledger_db, "2026-04", grocery_days=4, grocery_amount=20)
    for day in (6, 13, 20):
        add_tx(ledger_db, day=f"2026-04-{day:02d}", desc="TAKEOUT PLACE",
               amount=32, direction="debit", category="Restaurants",
               merchant="Takeout Place")
    ledger_db.commit()

    packet = _replay(ledger_db, month="2026-04")
    merchants = [t for t in packet["targets"] if t["kind"] == "merchant"]
    key = next(t["key"] for t in merchants if t["label"] == "Takeout Place")

    replay = _replay(ledger_db, month="2026-04", target_kind="merchant",
                     target_key=key, reduction_pct=100)["replay"]
    assert replay["available"], replay.get("reason")
    assert replay["matched_count"] == 3
    assert replay["freed"] == pytest.approx(96.0)


# ── ranking the month against its peers ───────────────────────────────────

def test_a_big_cut_moves_the_month_up_the_ranking(ledger_db):
    # April is the worst month until its groceries are removed.
    seed_month(ledger_db, "2026-02", grocery_days=4, grocery_amount=20)
    seed_month(ledger_db, "2026-03", grocery_days=4, grocery_amount=20)
    seed_month(ledger_db, "2026-04", grocery_days=20, grocery_amount=60)

    replay = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Groceries", reduction_pct=100)["replay"]

    assert replay["rank"]["total"] == 3
    assert replay["rank"]["actual"] == 3
    assert replay["rank"]["counterfactual"] == 1
    assert replay["rank"]["improved"] is True


# ── refusing what the data cannot support ─────────────────────────────────

def test_only_completed_months_are_offered(ledger_db):
    seed_month(ledger_db, "2026-03")
    # A thin partial month: two rows, no late-month anchor.
    add_tx(ledger_db, day="2026-04-02", desc="GROCER PARTIAL", amount=30,
           direction="debit", category="Groceries")
    ledger_db.commit()

    packet = _replay(ledger_db)
    offered = {row["month"] for row in packet["months"]}
    assert "2026-03" in offered
    assert "2026-04" not in offered


def test_no_finished_month_is_refused_with_a_reason(ledger_db):
    add_tx(ledger_db, day="2026-04-02", desc="GROCER PARTIAL", amount=30,
           direction="debit", category="Groceries")
    ledger_db.commit()

    packet = _replay(ledger_db)
    assert packet["available"] is False
    assert "finished month" in packet["reason"]
    assert packet["replay"] is None


@pytest.mark.parametrize("pct", [0, -10, 101, 1e9, float("nan"),
                                 float("inf")])
def test_implausible_reductions_are_refused(ledger_db, pct):
    seed_month(ledger_db, "2026-04")

    replay = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Groceries", reduction_pct=pct)["replay"]
    assert replay["available"] is False
    assert "between 1% and 100%" in replay["reason"]


def test_a_target_with_no_spending_says_so(ledger_db):
    seed_month(ledger_db, "2026-04")

    replay = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Skydiving", reduction_pct=100)["replay"]
    assert replay["available"] is False
    assert "nothing to replay" in replay["reason"]


def test_unknown_month_falls_back_to_the_latest_finished_one(ledger_db):
    seed_month(ledger_db, "2026-03")
    seed_month(ledger_db, "2026-04")

    packet = _replay(ledger_db, month="1999-01")
    assert packet["month"] == "2026-04"


# ── transfers stay excluded, as everywhere else ───────────────────────────

def test_internal_transfers_cannot_be_replayed(ledger_db):
    """seed_month includes a card payment and a savings transfer.

    Neither is spending, so neither may be offered as something to cut —
    "spend less on moving your own money" is not a financial change.
    """
    seed_month(ledger_db, "2026-04")

    packet = _replay(ledger_db, month="2026-04")
    labels = {t["label"] for t in packet["targets"]}
    assert "Credit Card Payment" not in labels
    assert "Internal Transfer" not in labels

    replay = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Internal Transfer",
                     reduction_pct=100)["replay"]
    assert replay["available"] is False


def test_a_shared_expense_only_frees_the_user_share(ledger_db):
    """Cutting a 50%-shared bill frees half of it, not all of it.

    The replay reads the same effective amount the rest of the app counts,
    so a shared cost cannot pay the user back for their partner's portion.
    """
    seed_month(ledger_db, "2026-04", grocery_days=2, grocery_amount=10)
    add_tx(ledger_db, day="2026-04-12", desc="SHARED HYDRO BILL", amount=200,
           direction="debit", category="Utilities / Bills",
           merchant="Shared Hydro")
    ledger_db.execute(
        "UPDATE transactions SET shared_expense_override=1, "
        "shared_user_share_pct=50 WHERE raw_description='SHARED HYDRO BILL'"
    )
    ledger_db.commit()

    packet = _replay(ledger_db, month="2026-04")
    key = next(t["key"] for t in packet["targets"]
               if t["label"] == "Shared Hydro")
    assert next(t["amount"] for t in packet["targets"]
                if t["key"] == key) == pytest.approx(100.0)

    replay = _replay(ledger_db, month="2026-04", target_kind="merchant",
                     target_key=key, reduction_pct=100)["replay"]
    assert replay["freed"] == pytest.approx(100.0)
    assert replay["net_delta"] == pytest.approx(100.0)


def test_evidence_lists_the_matched_transactions(ledger_db):
    seed_month(ledger_db, "2026-04", grocery_days=3, grocery_amount=40)

    replay = _replay(ledger_db, month="2026-04", target_kind="category",
                     target_key="Groceries", reduction_pct=50)["replay"]

    assert len(replay["transactions"]) == 3
    assert replay["evidence_truncated"] is False
    first = replay["transactions"][0]
    assert first["amount"] == pytest.approx(40.0)
    assert first["replayed_amount"] == pytest.approx(20.0)
