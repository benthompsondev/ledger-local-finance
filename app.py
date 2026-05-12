"""
Ledger — Personal Finance Dashboard
Entry point. Run with: streamlit run app.py

This file is intentionally thin. It declares the Streamlit sidebar
groups and routes each item to the page that owns the actual feature:

  • Declares which pages appear in the sidebar and in what order.
  • Groups pages so deep analytics (Spending / Income / Trends /
    Money Moves) live under "Reports", and developer-ish surfaces
    (Review / Diagnostics) live under "Tools".
  • Sets `pages/1_Dashboard.py` as the default page.
"""
import streamlit as st

st.set_page_config(
    page_title="Ledger · Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Each st.Page maps a sidebar entry to the script file that runs when
# selected. Dashboard is the first-load page.
_pages = {
    "Ledger": [
        st.Page("pages/1_Dashboard.py",       title="Dashboard",
                icon="🏠", default=True),
        st.Page("pages/3_Import.py",          title="Import",
                icon="📥"),
        st.Page("pages/2_Transactions.py",    title="Transactions",
                icon="📋"),
        st.Page("pages/11_Reduce.py",         title="Reduce",
                icon="✂️"),
        st.Page("pages/12_Month_Plan.py",     title="Plan",
                icon="🗓"),
        st.Page("pages/7_Investments.py",     title="Net Worth",
                icon="📈"),
        st.Page("pages/13_Reports.py",        title="Reports",
                icon="📊"),
        st.Page("pages/9_Settings.py",        title="Settings",
                icon="⚙️"),
    ],
    "Reports": [
        st.Page("pages/5_Spending.py",        title="Spending",
                icon="💸"),
        st.Page("pages/6_Income.py",          title="Income",
                icon="💵"),
        st.Page("pages/4_Trends.py",          title="Trends",
                icon="📉"),
        st.Page("pages/10_Recommendations.py", title="Money Moves",
                icon="💡"),
    ],
    "Tools": [
        st.Page("pages/8_Review.py",          title="Review queue",
                icon="🔍"),
    ],
}

# Diagnostics is a developer/support surface. Keep it out of the daily
# user sidebar unless explicitly enabled.
import os as _os
if _os.environ.get("LEDGER_DEV_MODE", "").strip().lower() in {
    "1", "true", "yes", "on",
}:
    _pages["Tools"].append(
        st.Page("pages/14_Diagnostics.py",
                title="Developer Diagnostics", icon="🩺")
    )

_pg = st.navigation(_pages, expanded=False)
_pg.run()
