"""What counts as worth telling someone about.

Two failures motivated these. A category moving from a handful of dollars to
a slightly larger handful was being announced as a four-figure percentage,
which reads as a crisis and describes two coffees. And materiality was a flat
$25 for everyone, so the same movement was a headline for a household
spending $800 a month and noise for one spending $6,000.

Both are about the same thing: a finding has to be worth the reader's
attention, and neither a percentage nor a dollar amount means anything
without the size of the thing it came from.

All figures here are synthetic.
"""
from __future__ import annotations

import pytest

from config.constants import MATERIALITY_THRESHOLD
from utils.checkin import (
    PERCENT_BASELINE_FLOOR, _change_phrase, _material_floor,
)


# ── percentages need a baseline that can carry one ──────────────────────

def test_a_tiny_baseline_is_reported_in_dollars_only():
    phrase = _change_phrase(60.0, 4.0)

    assert "$60" in phrase and "$4" in phrase
    assert "%" not in phrase, (
        "a move from $4 to $60 is +1400%, which describes almost nothing"
    )


def test_a_real_baseline_still_gets_its_percentage():
    phrase = _change_phrase(720.0, 480.0)

    assert "%" in phrase
    assert "+50%" in phrase


def test_the_baseline_floor_is_the_boundary():
    assert "%" not in _change_phrase(200.0, PERCENT_BASELINE_FLOOR - 1)
    assert "%" in _change_phrase(200.0, PERCENT_BASELINE_FLOOR + 1)


def test_something_genuinely_new_says_so_rather_than_dividing_by_zero():
    phrase = _change_phrase(140.0, 0.0)

    assert "New this month" in phrase
    assert "%" not in phrase
    assert "inf" not in phrase.lower()


def test_a_decrease_keeps_its_sign():
    phrase = _change_phrase(300.0, 600.0)

    assert "-50%" in phrase


# ── materiality scales with the person ──────────────────────────────────

def test_a_light_spender_keeps_the_flat_floor():
    """The constant is a floor, so a quiet month cannot make $3 material."""
    assert _material_floor(0) == MATERIALITY_THRESHOLD
    assert _material_floor(400) == MATERIALITY_THRESHOLD


def test_a_heavy_spender_needs_a_bigger_move_to_be_worth_saying():
    floor = _material_floor(6000)

    assert floor > MATERIALITY_THRESHOLD
    assert floor == pytest.approx(90.0, abs=0.01)


def test_the_floor_never_drops_below_the_constant():
    for spending in (0, 10, 500, 1200, 5000, 20000):
        assert _material_floor(spending) >= MATERIALITY_THRESHOLD


def test_the_floor_rises_with_spending_rather_than_jumping():
    low = _material_floor(2000)
    high = _material_floor(8000)

    assert high > low
    assert high / low == pytest.approx(4.0, abs=0.2)
