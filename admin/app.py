"""Lustdex admin — Tags section.

Run from the project root:
    streamlit run admin/app.py
"""

import sys
from pathlib import Path

# Project root must be in sys.path so pipeline.common.* is importable.
# Streamlit adds admin/ to sys.path; we add the parent (project root) here.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline.common.config  # noqa: F401  loads DATABASE_URL from .env
import streamlit as st

from tags import dictionary, overview, review, runs

_PAGES = {
    "Overview":   overview.render,
    "Review":     review.render,
    "Dictionary": dictionary.render,
    "Runs":       runs.render,
}

st.set_page_config(page_title="Lustdex Admin", layout="wide")
st.sidebar.title("Lustdex Admin")
st.sidebar.subheader("Tags")
page = st.sidebar.radio("Section", list(_PAGES.keys()), label_visibility="collapsed")
_PAGES[page]()
