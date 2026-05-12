"""
config/theme.py — Ledger design tokens.

Single source of truth for all colors, type scales, and chart primitives.
Import from here in charts.py, styles.py, and any page that needs brand colors.

To customize:
  - Edit CATEGORY_COLORS to remap any category to a different hex
  - Edit ACCENT / ACCENT2 for the primary brand green + blue
  - SURFACE_* controls card/panel backgrounds
"""

# ── Brand accents ─────────────────────────────────────────────────────────────
ACCENT        = "#34d058"   # primary green — income, positive, CTAs
ACCENT2       = "#60a5fa"   # secondary blue — net, info, links
SPEND_COLOR   = "#f87171"   # spending red
INCOME_COLOR  = ACCENT
NET_COLOR     = ACCENT2

# ── Backgrounds ───────────────────────────────────────────────────────────────
BG_PAGE       = "#0d1117"   # page / root background (matches config.toml)
BG_SURFACE    = "#161b22"   # sidebar, panel surfaces
BG_CARD       = "#1c2333"   # metric cards, expanders
BG_CHART      = "rgba(0,0,0,0)"  # transparent → inherits dark bg

# ── Typography ────────────────────────────────────────────────────────────────
TEXT_BASE     = "#e6edf3"
TEXT_MUTED    = "#8b949e"
FONT_FAMILY   = "Inter, 'Segoe UI', system-ui, -apple-system, sans-serif"
FONT_SIZE     = 12

# ── Borders / grids ───────────────────────────────────────────────────────────
BORDER        = "rgba(255,255,255,0.08)"
GRID_COLOR    = "rgba(255,255,255,0.07)"

# ── Category colour palette ───────────────────────────────────────────────────
# Keys match EXACTLY what the categorizer writes into the database.
# Principle: warm tones = real spending, cool = services/utilities,
#            green = income/positive, red = fees/risk,
#            slate/zinc = system categories (transfers, payments, cancelled)
#            GREY reserved only for Uncategorized / Other / truly unknown
CATEGORY_COLORS: dict[str, str] = {
    # ── Real spending categories (what actually appears in the DB) ─────────
    "Groceries":              "#34d058",   # fresh green
    "Food & Convenience":     "#fb923c",   # amber-orange  (convenience stores, fast food)
    "Shopping":               "#fb7185",   # rose-red
    "Gas / Transport":        "#3b82f6",   # bright blue
    "Health / Care":          "#f472b6",   # pink
    "Subscriptions & Digital":"#22d3ee",   # cyan
    "Housing / Mortgage":     "#a78bfa",   # soft violet
    "Pets":                   "#86efac",   # pale green
    "Cash Advance":           "#f97316",   # vivid orange-red (attention)
    "Fees / Interest":        "#ef4444",   # bright red (cost signal)
    "Investments":            "#60a5fa",   # sky blue
    "Savings":                "#4ade80",   # light green

    # ── Income ────────────────────────────────────────────────────────────
    "Income":                 "#34d058",   # green


    # ── e-Transfer cashflow categories (real money to/from people) ──────────
    "Transfer In":            "#34d058",   # green — incoming from people (income-adjacent)
    "Transfer Out":           "#f97316",   # vivid orange — outgoing to people (spending-adjacent)

    # ── System / flow categories (neutral, not real spending) ─────────────
    "Transfer":               "#475569",   # dark slate
    "Payment":                "#64748b",   # slate
    "Credit Card Payment":    "#64748b",   # slate (same family as Payment)
    "Cancelled":              "#334155",   # darkest slate

    # ── Fallback (intentionally dull — should rarely appear) ──────────────
    "Uncategorized":          "#52525b",   # zinc-grey
    "Misc":                   "#52525b",   # zinc-grey
    "Other":                  "#475569",   # grouped-remainder bucket

    # ── Legacy / alias names (in case older data used different labels) ───
    # Keep these so charts never silently fall through to grey
    "Dining Out":             "#f97316",
    "Coffee & Drinks":        "#fb923c",
    "Housing":                "#a78bfa",
    "Utilities":              "#818cf8",
    "Subscriptions":          "#22d3ee",
    "Transport":              "#3b82f6",
    "Travel":                 "#06b6d4",
    "Health":                 "#f472b6",
    "Personal Care":          "#e879f9",
    "Fitness":                "#4ade80",
    "Electronics":            "#60a5fa",
    "Entertainment":          "#fbbf24",
    "Education":              "#6366f1",
    "Gifts":                  "#f0abfc",
    "Fees & Interest":        "#ef4444",
    "Insurance":              "#94a3b8",
    "Pet":                    "#86efac",
}

# ── Investment account palette (cycling) ─────────────────────────────────────
INVESTMENT_COLORS = [
    "#60a5fa",   # blue
    "#34d058",   # green
    "#a78bfa",   # violet
    "#fbbf24",   # amber
    "#f472b6",   # pink
    "#22d3ee",   # cyan
]


def cat_color(category: str) -> str:
    """Return the hex color for a category, defaulting to muted grey."""
    return CATEGORY_COLORS.get(category, "#94a3b8")


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    """Convert a hex color string to an rgba() CSS value."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"
