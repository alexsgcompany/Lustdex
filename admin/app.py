"""Lustdex admin — Tags section.

Run from the project root:
    streamlit run admin/app.py
"""

import sys
from pathlib import Path

# Project root must be in sys.path so pipeline.common.* is importable.
# Streamlit adds admin/ to sys.path; we add the parent (project root) here.
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from page_screens import builder
from tags import dictionary, overview, review, runs

import pipeline.common.config  # noqa: F401  loads DATABASE_URL from .env

_SECTIONS = {
    "Tags": {
        "Overview":   overview.render,
        "Review":     review.render,
        "Dictionary": dictionary.render,
        "Runs":       runs.render,
    },
    "Pages": {
        "Builder": builder.render,
    },
}

st.set_page_config(page_title="Lustdex Admin", layout="wide")
st.sidebar.title("Lustdex Admin")

section = st.sidebar.radio("Section", list(_SECTIONS.keys()), key="section")
pages = _SECTIONS[section]
st.sidebar.subheader(section)
page = st.sidebar.radio("Page", list(pages.keys()), label_visibility="collapsed", key="page")
pages[page]()
