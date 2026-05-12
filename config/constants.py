"""
Single source of truth for alert colors and numeric thresholds.

These values were previously duplicated across pages (Dashboard, Spending,
Income, Review, Trends). Import from here to keep the app visually and
numerically consistent.
"""

# ── Alert / status colors ─────────────────────────────────────────
ALERT_COLOR_OK      = "#34d058"   # green — on track, positive
ALERT_COLOR_WARNING = "#f59e0b"   # amber — near budget / caution
ALERT_COLOR_OVER    = "#ef4444"   # red   — over budget / error

# ── Priority colors (Recommendations, Review) ─────────────────────
PRIORITY_COLOR = {
    "high":   ALERT_COLOR_OVER,
    "medium": ALERT_COLOR_WARNING,
    "low":    ALERT_COLOR_OK,
}
PRIORITY_BG = {
    "high":   "rgba(239,68,68,0.07)",
    "medium": "rgba(245,158,11,0.07)",
    "low":    "rgba(52,208,88,0.07)",
}

# ── Price-change detection ────────────────────────────────────────
PRICE_INCREASE_THRESHOLD  = 1.10   # 10% swing between min and max
PRICE_INCREASE_MIN_AMOUNT = 10.0   # ignore sub-$10 noise

# ── Budget status thresholds ──────────────────────────────────────
BUDGET_NEAR_PCT = 80   # "near" budget warning at ≥80% used
