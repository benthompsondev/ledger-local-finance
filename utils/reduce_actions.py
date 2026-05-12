"""
utils/reduce_actions.py — Pass 28.

Shared first-action / difficulty maps for Reduce. Lifted out of
pages/11_Reduce.py so the Reduce page UI, agent_context, and OpenClaw
all read the same deterministic strings. Adding a new category here
makes it visible everywhere; no schema, no AI, no internet.

Public:
  CATEGORY_FIRST_ACTION  — dict[str, str]   first practical step per category
  CATEGORY_DIFFICULTY    — dict[str, str]   "easy" | "moderate" | "harder"
  first_action_for(cat)  — str              with safe fallback string
  difficulty_for(cat)    — str              with "moderate" fallback
"""
from __future__ import annotations

CATEGORY_FIRST_ACTION: dict[str, str] = {
    "Shopping":
        "Pause one online order this week and audit the cart before checkout.",
    "Groceries":
        "Plan two meals from your pantry this week and skip one grocery run.",
    "Food & Convenience":
        "Pack lunch 3x this week — every $15 lunch saved is ~$45 weekly.",
    "Subscriptions & Digital":
        "Open Active Cancellation Candidates and cancel one unused service.",
    "Health / Care":
        "Check whether any monthly charges here are reimbursable via insurance.",
    "Gas / Transport":
        "Combine 2 errand trips into 1; compare gas prices on your usual route.",
    "Pets":
        "Stretch one bulk-order interval (food, treats) by an extra week.",
    "Entertainment":
        "Skip one paid event this week; substitute a free alternative.",
    "Home Improvement":
        "Defer one non-urgent project to next month; consolidate parts orders.",
    "Uncategorized":
        "Open Review and categorize the largest uncategorized rows — fixes targets.",
}

CATEGORY_DIFFICULTY: dict[str, str] = {
    "Subscriptions & Digital": "easy",
    "Uncategorized":           "easy",
    "Food & Convenience":      "moderate",
    "Shopping":                "moderate",
    "Pets":                    "moderate",
    "Entertainment":           "moderate",
    "Groceries":               "harder",
    "Gas / Transport":         "harder",
    "Health / Care":           "harder",
    "Home Improvement":        "harder",
}


def first_action_for(category: str | None) -> str:
    """Return the deterministic first-action string for a category.
    Falls back to a safe generic instruction so OpenClaw never gets
    an empty string."""
    if not category:
        return "Open Transactions and review the 5 largest charges."
    return CATEGORY_FIRST_ACTION.get(
        category,
        f"Open Transactions filtered to {category} and review the 5 largest charges.",
    )


def difficulty_for(category: str | None) -> str:
    """Return one of 'easy' | 'moderate' | 'harder' for a category."""
    if not category:
        return "moderate"
    return CATEGORY_DIFFICULTY.get(category, "moderate")
