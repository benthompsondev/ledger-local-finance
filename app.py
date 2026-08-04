"""
Ledger — Personal Finance Dashboard
Entry point. Run with: streamlit run app.py

This file is intentionally thin. It declares the Streamlit sidebar
groups and routes each item to the page that owns the actual feature:

  • Declares which pages appear in the sidebar and in what order.
  • Primary group is the weekly loop: Home / Add Data / Plan / Goals /
    Net Worth. Transactions, review, and settings live under Manage;
    deeper reports remain under Analysis.
  • Sets `pages/0_Home.py` (the weekly check-in) as the default page.
"""
import streamlit as st

st.set_page_config(
    page_title="Ledger · Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Each st.Page maps a sidebar entry to the script file that runs when
# selected. Home (the weekly check-in) is the first-load page; the
# primary group stays small on purpose. Every pre-existing page remains
# reachable under Analysis — nothing was deleted, only re-homed.
_pages = {
    "Ledger": [
        st.Page("pages/0_Home.py",            title="Home",
                icon="✅", default=True),
        st.Page("pages/3_Import.py",          title="Add Data",
                icon="📥"),
        st.Page("pages/12_Month_Plan.py",     title="Plan",
                icon="🗓"),
        st.Page("pages/15_Goals.py",         title="Goals",
                icon="🎯"),
        st.Page("pages/7_Investments.py",     title="Net Worth",
                icon="📈"),
    ],
    "Analysis": [
        st.Page("pages/1_Dashboard.py",       title="Detailed overview",
                icon="🏠"),
        st.Page("pages/13_Reports.py",        title="Reports",
                icon="📊"),
        st.Page("pages/4_Trends.py",          title="Trends",
                icon="📉"),
        st.Page("pages/5_Spending.py",        title="Spending",
                icon="💸"),
        st.Page("pages/6_Income.py",          title="Income",
                icon="💵"),
        st.Page("pages/11_Reduce.py",         title="Reduce",
                icon="✂️"),
        st.Page("pages/10_Recommendations.py", title="Money Moves",
                icon="💡"),
    ],
    "Manage": [
        st.Page("pages/2_Transactions.py",    title="Transactions",
                icon="📋"),
        st.Page("pages/8_Review.py",          title="Review queue",
                icon="🔍"),
        st.Page("pages/9_Settings.py",        title="Settings",
                icon="⚙️"),
    ],
}

# Diagnostics is a developer/support surface. Keep it out of the daily
# user sidebar unless explicitly enabled.
import os as _os
if _os.environ.get("LEDGER_DEV_MODE", "").strip().lower() in {
    "1", "true", "yes", "on",
}:
    _pages["Manage"].append(
        st.Page("pages/14_Diagnostics.py",
                title="Developer Diagnostics", icon="🩺")
    )

_pg = st.navigation(_pages, expanded=False)
_pg.run()
