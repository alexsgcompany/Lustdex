import re

import streamlit as st
from pipeline.common.db import get_conn
from tags import queries

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def render() -> None:
    st.header("Review Queue")

    if "resolved_count" not in st.session_state:
        st.session_state.resolved_count = 0

    # ── filters ──────────────────────────────────────────────────────────────
    with get_conn() as conn:
        providers = conn.execute("SELECT id, name FROM providers ORDER BY name").fetchall()
        cats      = [r[0] for r in conn.execute(queries.DISTINCT_CATEGORIES).fetchall()]

    fc1, fc2, fc3 = st.columns(3)

    prov_options = [(None, "All")] + [(p[0], p[1]) for p in providers]
    prov_choice  = fc1.selectbox("Provider", prov_options,
                                 format_func=lambda x: x[1], key="rv_prov")
    sug_choice   = fc2.selectbox("Suggestion", ["Any", "Yes", "No"], key="rv_sug")
    min_freq     = fc3.number_input("Min freq", min_value=1, value=1, key="rv_freq")

    # ── build query ──────────────────────────────────────────────────────────
    params: list = []
    prov_clause = sug_clause = freq_clause = ""

    if prov_choice[0] is not None:
        prov_clause = "AND ut.providers @> ARRAY[%s]::int[]"
        params.append(prov_choice[0])
    if sug_choice == "Yes":
        sug_clause = "AND ut.suggested_tag_id IS NOT NULL"
    elif sug_choice == "No":
        sug_clause = "AND ut.suggested_tag_id IS NULL"
    freq_clause = "AND ut.freq >= %s"
    params.append(int(min_freq))

    sql = queries.REVIEW_QUEUE.format(
        provider_clause=prov_clause,
        suggestion_clause=sug_clause,
        freq_clause=freq_clause,
    )

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    # ── header ───────────────────────────────────────────────────────────────
    n = len(rows)
    st.write(f"**{n}** item{'s' if n != 1 else ''} in queue")
    if st.session_state.resolved_count:
        st.info(
            f"Resolved {st.session_state.resolved_count} tag(s) this session. "
            "Run **Apply** (Runs screen) to link new videos."
        )
    if st.button("Refresh queue"):
        st.rerun()

    # ── per-item rows ─────────────────────────────────────────────────────────
    for row in rows:
        normalized, freq, raw_examples, sug_id, sug_source, sug_conf, sug_slug, sug_name = row
        examples_str = ", ".join((raw_examples or [])[:4])
        with st.expander(f"**{normalized}**  (freq={freq})  —  _{examples_str}_"):
            _render_actions(normalized, freq, cats, sug_id, sug_source, sug_conf, sug_name)


# ── action panel ─────────────────────────────────────────────────────────────

def _render_actions(normalized, freq, cats, sug_id, sug_source, sug_conf, sug_name):
    top_col1, top_col2 = st.columns([4, 1])

    # Accept suggestion
    if sug_id:
        label = f"Accept → **{sug_name}** ({sug_conf:.0%}  via {sug_source})"
        if top_col1.button(label, key=f"acc_{normalized}", type="primary"):
            _resolve(normalized, sug_id, sug_source, sug_conf)
            st.rerun()
    else:
        top_col1.caption("No cascade suggestion")

    # Trash
    if top_col2.button("Trash", key=f"trash_{normalized}"):
        with get_conn() as conn:
            conn.execute(
                "UPDATE unmapped_tags SET status='trash', updated_at=now() WHERE normalized=%s",
                (normalized,),
            )
            conn.commit()
        st.rerun()

    st.divider()
    left, right = st.columns(2)

    # Map to existing
    with left:
        st.caption("Map to existing canonical")
        search = st.text_input("Search slug / name", key=f"search_{normalized}", label_visibility="collapsed",
                               placeholder="Search slug or name…")
        if search:
            with get_conn() as conn:
                candidates = conn.execute(queries.CANONICAL_SEARCH, (f"%{search}%", f"%{search}%")).fetchall()
            if candidates:
                choice = st.selectbox(
                    "Canonical", candidates,
                    format_func=lambda x: f"{x[1]}  —  {x[2]}",
                    key=f"choice_{normalized}",
                )
                if st.button("Map", key=f"map_{normalized}"):
                    _resolve(normalized, choice[0], "manual", 1.0)
                    st.rerun()
            else:
                st.caption("No matches.")

    # New canonical
    with right:
        st.caption("Create new canonical")
        with st.form(key=f"new_{normalized}"):
            slug_input = st.text_input("slug *", value=normalized.replace(" ", "-"),
                                       key=f"slug_{normalized}")
            name_input = st.text_input("name", value=normalized.replace("-", " ").title(),
                                       key=f"name_{normalized}")
            cat_input  = st.selectbox("category", [""] + cats, key=f"cat_{normalized}")
            if st.form_submit_button("Create & map"):
                slug = slug_input.strip()
                if not slug:
                    st.error("slug is required")
                elif not _SLUG_RE.match(slug):
                    st.error("slug must match: lowercase, digits, hyphens only (e.g. my-tag)")
                else:
                    with get_conn() as conn:
                        existing = conn.execute("SELECT id FROM tags WHERE slug=%s", (slug,)).fetchone()
                        if existing:
                            st.error(f"slug '{slug}' already exists — use Map to existing instead")
                        else:
                            name = name_input.strip() or slug.replace("-", " ").title()
                            cat  = cat_input or None
                            tag_id = conn.execute(
                                "INSERT INTO tags (slug, name, category) VALUES (%s, %s, %s) RETURNING id",
                                (slug, name, cat),
                            ).fetchone()[0]
                            conn.commit()
                        # resolve outside the if/else so slug-exists path doesn't fall through
                    if not existing:
                        _resolve(normalized, tag_id, "manual", 1.0)
                        st.rerun()


def _resolve(normalized: str, tag_id: int, source: str, confidence: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tag_aliases (normalized, tag_id, source, confidence)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (normalized) DO NOTHING",
            (normalized, tag_id, source, confidence),
        )
        conn.execute(
            "UPDATE unmapped_tags SET status='resolved', updated_at=now() WHERE normalized=%s",
            (normalized,),
        )
        conn.commit()
    st.session_state.resolved_count += 1
