"""
Statement-aware category taxonomy (Pass 17).

Two groups so the rest of the app can keep them apart:

  SPENDING_CATEGORIES       — what the user actually consumes; the only set
                              that should appear in budget dropdowns and as
                              "spending" on the dashboard.
  SYSTEM_CATEGORIES         — accounting / cashflow plumbing; visible in
                              Review / Transactions / Trends-as-needed but
                              never as a budget target or as consumption.

`CATEGORIES` is the union — kept for backward compatibility with code that
displays a full picker (Review, Transactions inline edit, etc.).
`BUDGETABLE_CATEGORIES` is the curated dropdown list for Settings → Budgets
(Pass 17 manual-test feedback: budget settings shouldn't expose Income /
Transfer / Credit Card Payment).

If you add a new category, decide which list it belongs in. If a value can
both consume money AND function as plumbing (none today), don't.
"""

# ── A. Spending / budgetable categories ────────────────────────────────
# These are the ONLY categories the user should see in budget dropdowns,
# as bars in the Spending page, or as cells in the Trends consumption
# matrix.
SPENDING_CATEGORIES = [
    "Housing / Mortgage",
    "Utilities / Bills",
    "Groceries",
    "Food & Convenience",
    "Shopping",
    "Subscriptions & Digital",
    "Entertainment",
    "Home Improvement",
    "Gas / Transport",
    "Health / Care",
    "Pets",
    "Fees / Interest",
    "Cash Advance",
    "Misc",
    "Uncategorized",
]

# Alias used in some places for "the curated list to show in budget UI".
BUDGETABLE_CATEGORIES = [c for c in SPENDING_CATEGORIES if c not in (
    "Uncategorized",                            # not a real budget target
)]

# ── B. System / accounting categories ──────────────────────────────────
# Real cashflow plumbing — visible to Review / Transactions but not to
# spending charts, budgets, or consumption comparison.
#
# Pass 18: Reimbursement / Insurance Reimbursement moved here from
# SPENDING_CATEGORIES. It was always direction-mapped as income (see
# CATEGORY_DIRECTION below), so listing it under spending was
# conceptually inconsistent — the user could and did accidentally
# budget against reimbursements. It's a credit/refund flow, not a
# consumption category, and it now joins the income-side accounting
# bucket.
SYSTEM_CATEGORIES = [
    "Income",                # generic; prefer Payroll/Interest/Rewards below
    "Payroll Income",
    "Interest Income",
    "Rewards / Cashback",
    "Reimbursement / Insurance Reimbursement",  # credit-side accounting
    "Transfer",              # generic
    "Transfer In",           # INTERAC e-Transfers received from people
    "Transfer Out",          # INTERAC e-Transfers sent to people
    "Internal Transfer",     # savings ↔ chequing
    "Credit Card Payment",
    "Refund / Credit",
    "Savings",
    "Investments",
    "Cancelled",
]

# ── C. Union — used by pickers that show every option ──────────────────
# Order: spending first (most-used in Review), then system. Misc /
# Uncategorized live at the tail of spending so they don't crowd the top.
CATEGORIES = [
    *SPENDING_CATEGORIES,
    *SYSTEM_CATEGORIES,
]
# De-dupe just in case a category accidentally appears in both lists.
_seen = set()
CATEGORIES = [c for c in CATEGORIES if not (c in _seen or _seen.add(c))]
del _seen

# ── D. Compatibility sets used by analytics / insights ─────────────────
# Anything in here is excluded from spending sums and consumption charts.
# Pass 17 expanded the set to cover the new system categories so analytics
# never accidentally counts an internal transfer as consumption.
NON_SPENDING_CATEGORIES = {
    "Income",
    "Payroll Income",
    "Interest Income",
    "Rewards / Cashback",
    "Reimbursement / Insurance Reimbursement",  # Pass 18: credit-side accounting
    "Transfer",
    "Transfer In",
    "Transfer Out",
    "Internal Transfer",
    "Credit Card Payment",
    "Refund / Credit",
    "Savings",
    "Investments",
    "Cancelled",
}

# Categories that should never appear in a CONSUMPTION comparison (Trends,
# Reduce, Spending bar). Subset of the above; keeps Cash Advance / Fees as
# "consumption" for the purpose of tracking how much you're paying in fees.
NON_CONSUMPTION_CATEGORIES = NON_SPENDING_CATEGORIES

# ── E. Direction overrides per category ────────────────────────────────
# Some categories must always be associated with a specific cashflow
# direction regardless of amount sign (matches enrich_transaction).
CATEGORY_DIRECTION = {
    "Income":              "income",
    "Payroll Income":      "income",
    "Interest Income":     "income",
    "Rewards / Cashback":  "income",
    "Reimbursement / Insurance Reimbursement": "income",
    "Refund / Credit":     "income",
    "Credit Card Payment": "payment",
    "Transfer":            "transfer",
    "Internal Transfer":   "transfer",
    "Savings":             "transfer",
    "Investments":         "transfer",
    "Fees / Interest":     "expense",
    "Cash Advance":        "expense",
}

# How categories map to cash flow groups on the dashboard. Pass 17 splits
# Utilities out of Housing and adds the new income-style buckets.
CASHFLOW_GROUPS = {
    "Income":              ["Income", "Payroll Income", "Interest Income",
                            "Rewards / Cashback",
                            "Reimbursement / Insurance Reimbursement",
                            "Refund / Credit"],
    "Housing":             ["Housing / Mortgage"],
    "Utilities":           ["Utilities / Bills"],
    "Variable Spending":   [
        "Groceries", "Food & Convenience", "Gas / Transport",
        "Shopping", "Subscriptions & Digital", "Entertainment",
        "Home Improvement", "Health / Care", "Pets", "Misc",
    ],
    "Fees":                ["Fees / Interest", "Cash Advance"],
    "Transfers":           ["Transfer", "Transfer In", "Transfer Out",
                            "Internal Transfer"],
    "Card Payments":       ["Credit Card Payment"],
    "Savings/Investments": ["Savings", "Investments"],
}
