"""
styles.py — global CSS injection for Ledger.
Call inject_styles() at the top of every page (after set_page_config).

Safe rules only — avoid selectors that fight Streamlit's own component internals:
  - Do NOT target `summary` on expanders (causes double arrow-right text)
  - Do NOT target file uploader button via baseButton-primary (causes double "upload" text)
  - Use data-testid selectors that are stable across Streamlit 1.33+

All color tokens imported from config.theme — edit there to restyle everything.
"""
import streamlit as st
from config.theme import (
    ACCENT, ACCENT2,
    BG_SURFACE, BG_CARD, BG_PAGE,
    BORDER, TEXT_BASE, TEXT_MUTED,
    FONT_FAMILY,
)

CSS = f"""
<style>
/* ── Font ─────────────────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif !important;
}}

/* ── Main content padding ─────────────────────────────────────────── */
.main .block-container {{
    padding-top: 1.6rem !important;
    padding-bottom: 3.5rem !important;
    padding-left: 2.2rem !important;
    padding-right: 2.2rem !important;
    max-width: 1320px !important;
}}

/* ── Page title ───────────────────────────────────────────────────── */
h1 {{
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.025em !important;
    color: {TEXT_BASE} !important;
    margin-bottom: 0.25rem !important;
    line-height: 1.2 !important;
}}

/* ── Section headers (h2 / h3) ────────────────────────────────────── */
h2 {{
    font-size: 1.0rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
    color: {TEXT_BASE} !important;
    margin-top: 1.4rem !important;
    margin-bottom: 0.5rem !important;
    padding-bottom: 0.35rem !important;
    border-bottom: 1px solid {BORDER} !important;
}}

h3 {{
    font-size: 0.92rem !important;
    font-weight: 600 !important;
    color: {TEXT_BASE} !important;
    margin-top: 1.0rem !important;
    margin-bottom: 0.35rem !important;
}}

/* ── Metric cards ─────────────────────────────────────────────────── */
[data-testid="metric-container"] {{
    background: {BG_CARD} !important;
    border: 1px solid {BORDER} !important;
    border-left: 3px solid {ACCENT} !important;
    border-radius: 10px !important;
    padding: 1.05rem 1.25rem 0.9rem !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35), 0 0 0 1px rgba(255,255,255,0.04) inset !important;
    transition: box-shadow 0.15s ease !important;
}}

[data-testid="metric-container"]:hover {{
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(255,255,255,0.06) inset !important;
}}

[data-testid="metric-container"] label {{
    font-size: 0.67rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.07em !important;
    color: {TEXT_MUTED} !important;
}}

[data-testid="metric-container"] [data-testid="stMetricValue"] {{
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    color: {TEXT_BASE} !important;
    line-height: 1.15 !important;
}}

[data-testid="metric-container"] [data-testid="stMetricDelta"] {{
    font-size: 0.70rem !important;
}}

/* ── Column gap fix — give metric cards breathing room ────────────── */
[data-testid="column"] {{
    padding-left: 0.4rem !important;
    padding-right: 0.4rem !important;
}}

/* ── Sidebar ──────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {{
    background: {BG_SURFACE} !important;
    border-right: 1px solid {BORDER} !important;
}}

/* ── Tabs ─────────────────────────────────────────────────────────── */
[data-testid="stTabs"] [role="tab"] {{
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    padding: 0.45rem 0.9rem !important;
}}

[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
    color: {ACCENT} !important;
    border-bottom-color: {ACCENT} !important;
}}

/* ── Primary buttons — ONLY target stButton wrappers, not file uploader ── */
[data-testid="stButton"] > button[kind="primary"] {{
    background: {ACCENT} !important;
    border: none !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    border-radius: 6px !important;
    color: #0d1117 !important;
    padding: 0.4rem 1rem !important;
}}

[data-testid="stButton"] > button[kind="secondary"] {{
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid {BORDER} !important;
    font-weight: 500 !important;
    font-size: 0.82rem !important;
    border-radius: 6px !important;
    color: {TEXT_BASE} !important;
}}

/* ── Expanders — style the container only, NOT the summary arrow ──── */
[data-testid="stExpander"] {{
    background: {BG_SURFACE} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 8px !important;
    margin-bottom: 0.6rem !important;
}}

/* ── Data tables ──────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {{
    border: 1px solid {BORDER} !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}}

/* ── Alerts ───────────────────────────────────────────────────────── */
[data-testid="stAlert"] {{
    border-radius: 8px !important;
    font-size: 0.85rem !important;
}}

/* ── Dividers ─────────────────────────────────────────────────────── */
hr {{
    border-color: {BORDER} !important;
    margin: 1.1rem 0 !important;
}}

/* ── Caption ──────────────────────────────────────────────────────── */
[data-testid="stCaptionContainer"] p {{
    font-size: 0.75rem !important;
    color: {TEXT_MUTED} !important;
}}

/* ── Plotly chart container ───────────────────────────────────────── */
[data-testid="stPlotlyChart"] > div {{
    border-radius: 10px !important;
}}

/* ── Section label helper ─────────────────────────────────────────── */
.ledger-section-header {{
    font-size: 0.66rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: {TEXT_MUTED};
    margin-bottom: 0.4rem;
    margin-top: 1.1rem;
    padding-bottom: 0.2rem;
    border-bottom: 1px solid {BORDER};
}}

/* ── Custom card wrapper ──────────────────────────────────────────── */
.ledger-card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.85rem;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.30);
}}

/* ── Page section separator ───────────────────────────────────────── */
.ledger-separator {{
    height: 1px;
    background: {BORDER};
    margin: 1.4rem 0 1.0rem 0;
    border: none;
}}

/* ── Muted helper text ────────────────────────────────────────────── */
.ledger-muted {{
    font-size: 0.78rem;
    color: {TEXT_MUTED};
    line-height: 1.5;
}}
</style>
"""


def inject_styles():
    """Inject Ledger's global CSS. Call once per page, after set_page_config.

    Pass 29: also surfaces the global Demo Mode banner here so EVERY
    page that calls inject_styles automatically gets the banner when
    LEDGER_DEMO_DB=1. Previously the banner only rendered on app.py
    (the 'Today' home), so users who navigated to a sub-page lost the
    visual signal that they were looking at synthetic data.
    """
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        from utils.database import is_demo_mode, DB_PATH as _DBP
        if is_demo_mode():
            st.markdown(
                f"<div style='background:rgba(243,139,57,0.10);"
                f"border:1px solid rgba(243,139,57,0.45);"
                f"border-left:3px solid #f38b39;border-radius:8px;"
                f"padding:6px 12px;margin-bottom:10px;font-size:0.82rem'>"
                f"<b style='color:#f38b39'>DEMO MODE</b>"
                f"<span style='color:#c9d1d9'> · "
                f"reading <code>{_DBP.name}</code> — every merchant is "
                f"fictional. Unset <code>LEDGER_DEMO_DB</code> to switch "
                f"back to your real ledger.</span></div>",
                unsafe_allow_html=True,
            )
    except Exception:
        pass
