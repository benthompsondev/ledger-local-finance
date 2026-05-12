"""
streamlit_app.py — Streamlit Cloud entry point.

Streamlit Community Cloud looks for this filename by convention.
This simply re-exports the main app module so both:
  - Local: `streamlit run app.py`
  - Cloud: `streamlit run streamlit_app.py`
work identically.
"""
# Import and run app.py as if it were the entry point
import runpy, sys, os

# Ensure the package root is on sys.path regardless of how we're invoked
sys.path.insert(0, os.path.dirname(__file__))

runpy.run_path(os.path.join(os.path.dirname(__file__), "app.py"), run_name="__main__")
