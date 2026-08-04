"""Known shapes of real bank exports.

Some things about a CSV cannot be worked out by looking at it. Discover
writes purchases as positive numbers while Chase writes the same purchase as
negative, and nothing in either file says which convention is in use: a
column of positive amounts is equally consistent with "this is a card
statement where everything is a purchase" and "this person only received
money this month". Guessing turns every purchase into income.

Headerless exports have the same problem from the other direction. TD and
Wells Fargo both produce five unlabelled columns, and only the arrangement of
text and numbers tells them apart.

So the shapes are written down. A file that matches a known bank is read with
that bank's conventions and says so; a file that matches nothing falls back
to detection, which is still where most exports end up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# What a column holds, used to tell headerless layouts apart.
#   date  parses as a date
#   num   parses as a number (may be blank on some rows)
#   text  has letters
#   any   unchecked
COLUMN_KINDS = ("date", "num", "text", "any")


@dataclass(frozen=True)
class BankPreset:
    key: str
    name: str
    country: str
    # A file matches when every one of these normalised headers is present.
    header_signature: tuple[str, ...] = ()
    # Extra headers that must NOT be present, to separate near-identical banks.
    header_absent: tuple[str, ...] = ()
    # Role per column position, for exports with no header row at all.
    positional_roles: tuple[str, ...] = ()
    # Expected content of each column, same order, for telling those apart.
    positional_kinds: tuple[str, ...] = ()
    # Column name overrides applied after the usual alias mapping.
    column_roles: dict = field(default_factory=dict)
    date_format: Optional[str] = None
    # "standard"  a negative amount left the account
    # "inverted"  a positive amount left the account (card statements)
    amount_sign: str = "standard"
    note: str = ""


PRESETS: tuple[BankPreset, ...] = (
    # ── Canada ──────────────────────────────────────────────────────────
    BankPreset(
        key="tangerine", name="Tangerine", country="CA",
        header_signature=("date", "transaction", "name", "memo", "amount"),
        date_format="%m/%d/%Y",
        note="Name holds the merchant; Memo is free text.",
    ),
    BankPreset(
        key="rbc", name="RBC Royal Bank", country="CA",
        header_signature=("account type", "transaction date", "cad$"),
        # Both currency columns otherwise map to the amount and the empty one
        # wins, which left every row at zero.
        column_roles={"cad$": "amount", "usd$": "amount_usd",
                      "description 1": "raw_description",
                      "description 2": "raw_description_2"},
        date_format="%m/%d/%Y",
        note="Separate CAD and USD columns; only CAD is read.",
    ),
    BankPreset(
        key="bmo", name="BMO", country="CA",
        header_signature=("first bank card", "transaction type", "date posted",
                          "transaction amount"),
        note="Dates arrive as YYYYMMDD.",
    ),
    BankPreset(
        key="cibc", name="CIBC", country="CA",
        positional_roles=("transaction_date", "raw_description", "debit",
                          "credit"),
        positional_kinds=("date", "text", "num", "num"),
        note="No header row: date, description, money out, money in.",
    ),
    BankPreset(
        key="td", name="TD Canada Trust", country="CA",
        positional_roles=("transaction_date", "raw_description", "debit",
                          "credit", "balance"),
        positional_kinds=("date", "text", "num", "num", "num"),
        note="No header row, with a running balance in the last column.",
    ),
    BankPreset(
        key="scotiabank", name="Scotiabank", country="CA",
        positional_roles=("transaction_date", "amount", "raw_description"),
        positional_kinds=("date", "num", "text"),
        note="No header row: date, amount, description.",
    ),
    # ── United States ───────────────────────────────────────────────────
    BankPreset(
        key="chase", name="Chase", country="US",
        header_signature=("posting date", "description", "amount", "type"),
        date_format="%m/%d/%Y",
        note="Purchases are negative.",
    ),
    BankPreset(
        key="bofa", name="Bank of America", country="US",
        header_signature=("date", "description", "amount", "running bal."),
        date_format="%m/%d/%Y",
        note="Account summary lines above the header are skipped.",
    ),
    BankPreset(
        key="citi", name="Citi", country="US",
        header_signature=("status", "date", "description", "debit", "credit"),
        date_format="%m/%d/%Y",
        note="Separate debit and credit columns.",
    ),
    BankPreset(
        key="capitalone", name="Capital One", country="US",
        header_signature=("transaction date", "posted date", "card no.",
                          "debit", "credit"),
        note="Separate debit and credit columns.",
    ),
    BankPreset(
        key="discover", name="Discover", country="US",
        header_signature=("trans. date", "post date", "description", "amount"),
        date_format="%m/%d/%Y",
        amount_sign="inverted",
        note="Discover writes purchases as POSITIVE; the sign is flipped so "
             "a purchase is money out.",
    ),
    BankPreset(
        key="amex", name="American Express", country="US",
        # Deliberately anchored on "card member", which is Amex's own field.
        # Matching on date/description/amount alone would claim the most
        # common header in existence, and because this preset flips the sign
        # it would turn every ordinary export inside out.
        header_signature=("date", "description", "amount", "card member"),
        date_format="%m/%d/%Y",
        amount_sign="inverted",
        note="Amex writes charges as POSITIVE; the sign is flipped so a "
             "charge is money out.",
    ),
    BankPreset(
        key="usbank", name="U.S. Bank", country="US",
        header_signature=("date", "transaction", "name", "memo", "amount"),
        header_absent=("running bal.",),
        date_format="%m/%d/%Y",
        note="Same layout as Tangerine.",
    ),
    BankPreset(
        key="wellsfargo", name="Wells Fargo", country="US",
        positional_roles=("transaction_date", "amount", "ignore_1",
                          "ignore_2", "raw_description"),
        positional_kinds=("date", "num", "any", "any", "text"),
        date_format="%m/%d/%Y",
        note="No header row, with two unused columns in the middle.",
    ),
    # ── elsewhere ───────────────────────────────────────────────────────
    # Australian and British exports are day-first, which a file covering a
    # single month cannot always prove on its own. Naming the bank settles it.
    BankPreset(
        key="commbank", name="Commonwealth Bank", country="AU",
        positional_roles=("transaction_date", "amount", "raw_description",
                          "balance"),
        positional_kinds=("date", "num", "text", "num"),
        date_format="%d/%m/%Y",
        note="No header row: date, amount, description, balance.",
    ),
    BankPreset(
        key="starling", name="Starling Bank", country="GB",
        header_signature=("date", "counter party", "reference", "type"),
        date_format="%d/%m/%Y",
        note="Dates are day-first.",
    ),
    BankPreset(
        key="monzo", name="Monzo", country="GB",
        header_signature=("transaction id", "date", "time", "type", "name"),
        date_format="%d/%m/%Y",
        note="Dates are day-first.",
    ),
    BankPreset(
        key="barclays", name="Barclays", country="GB",
        header_signature=("number", "date", "account", "amount", "memo"),
        date_format="%d/%m/%Y",
        note="Dates are day-first.",
    ),
)

_DATEISH = re.compile(
    r"^\s*(\d{1,4}[-/]\d{1,2}[-/]\d{1,4}|\d{8})"
)
_NUMBERISH = re.compile(r"^\s*[-+(]?\s*[A-Z]{0,3}\s*[\d.,]+\s*[-+)]?\s*$", re.I)


def _normalise(header: str) -> str:
    return str(header or "").strip().lower().replace("_", " ")


def _looks(kind: str, values: list[str]) -> bool:
    """Does this column's content match the expected kind?"""
    filled = [v for v in (str(x or "").strip() for x in values) if v]
    if kind == "any":
        return True
    if not filled:
        # A column that is empty everywhere can still be a money column that
        # simply had no activity, but never a date or a description.
        return kind == "num"
    if kind == "date":
        return all(_DATEISH.match(v) for v in filled)
    if kind == "num":
        return all(_NUMBERISH.match(v) for v in filled)
    if kind == "text":
        return sum(1 for v in filled if any(c.isalpha() for c in v)) >= max(
            1, len(filled) // 2
        )
    return True


def match_header_preset(header: list[str]) -> Optional[BankPreset]:
    """Find the bank whose header this file carries."""
    present = {_normalise(h) for h in header}
    matches = [
        preset for preset in PRESETS
        if preset.header_signature
        and set(preset.header_signature) <= present
        and not (set(preset.header_absent) & present)
    ]
    if not matches:
        return None
    # The most specific signature wins, so Chase does not lose to a shorter
    # pattern that happens to be a subset of it.
    matches.sort(key=lambda p: -len(p.header_signature))
    tied = [
        p for p in matches
        if len(p.header_signature) == len(matches[0].header_signature)
    ]
    if len(tied) > 1:
        return _resolve_tie(tied)
    return matches[0]


def _behaviour(preset: BankPreset) -> tuple:
    """What actually changes how a file is read."""
    return (
        tuple(sorted(preset.column_roles.items())),
        preset.positional_roles,
        preset.date_format,
        preset.amount_sign,
    )


def _resolve_tie(tied: list[BankPreset]) -> Optional[BankPreset]:
    """Two banks can publish the same layout, which is not a problem.

    Tangerine and U.S. Bank use identical columns and the same sign
    convention, so a file from either is read the same way. Refusing to
    choose would throw away a correct answer over a distinction that changes
    nothing. Only a tie that would parse differently is a real ambiguity.
    """
    if len({_behaviour(p) for p in tied}) > 1:
        return None
    names = " or ".join(p.name for p in sorted(tied, key=lambda p: p.name))
    first = tied[0]
    return BankPreset(
        key="|".join(sorted(p.key for p in tied)),
        name=names,
        country=first.country,
        header_signature=first.header_signature,
        header_absent=first.header_absent,
        positional_roles=first.positional_roles,
        positional_kinds=first.positional_kinds,
        column_roles=dict(first.column_roles),
        date_format=first.date_format,
        amount_sign=first.amount_sign,
        note="These banks export the same layout, so it is read the same way.",
    )


def match_positional_preset(rows: list[list[str]]) -> Optional[BankPreset]:
    """Find the bank whose headerless layout this file has.

    Returns None when two banks fit equally well, so an ambiguous file goes
    to the mapping step instead of being silently read as the wrong bank.
    """
    if not rows:
        return None
    sample = [r for r in rows[:25] if any(str(c).strip() for c in r)]
    if not sample:
        return None
    width = max(len(r) for r in sample)
    columns = [
        [row[i] if i < len(row) else "" for row in sample]
        for i in range(width)
    ]
    matches = [
        preset for preset in PRESETS
        if preset.positional_kinds
        and len(preset.positional_kinds) == width
        and all(
            _looks(kind, columns[i])
            for i, kind in enumerate(preset.positional_kinds)
        )
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def preset_by_key(key: str) -> Optional[BankPreset]:
    return next((p for p in PRESETS if p.key == key), None)


def preset_choices() -> list[dict]:
    """For the import screen, when the user wants to pick the bank."""
    return [
        {"value": p.key, "label": p.name, "country": p.country,
         "note": p.note}
        for p in sorted(PRESETS, key=lambda p: (p.country, p.name))
    ]
