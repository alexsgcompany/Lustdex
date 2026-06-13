import re

import streamlit as st

from pipeline.common.db import get_conn
from tags import queries

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def render() -> None:
    st.header("Dictionary")

    sc1, sc2 = st.columns(2)
    search     = sc1.text_input("Search", placeholder="slug or name", key="dict_search")
    with get_conn() as conn:
        cats = [r[0] for r in conn.execute(queries.DISTINCT_CATEGORIES).fetchall()]
    cat_filter = sc2.selectbox("Category", [""] + cats, key="dict_cat",
                               format_func=lambda x: "All" if not x else x)

    # ── build list query ───────────────────────────────────────────────────
    where_parts: list[str] = []
    params: list = []
    if search:
        where_parts.append("(t.slug ILIKE %s OR t.name ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if cat_filter:
        where_parts.append("t.category = %s")
        params.append(cat_filter)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sql = queries.DICTIONARY_LIST.format(where=where_sql)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    st.write(f"**{len(rows)}** canonical tag{'s' if len(rows) != 1 else ''}")

    selected = st.session_state.get("dict_tag_id")

    for tag_id, slug, name, category, occ_freq in rows:
        c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
        c1.write(f"**{name}**  `{slug}`")
        c2.write(f"{category or '—'}")
        c3.write(f"{occ_freq:,}")
        toggle_label = "Close" if selected == tag_id else "Detail"
        if c4.button(toggle_label, key=f"det_{tag_id}"):
            st.session_state.dict_tag_id = None if selected == tag_id else tag_id
            st.rerun()

        if selected == tag_id:
            _render_detail(tag_id, slug, name, category, cats)


def _render_detail(tag_id: int, slug: str, name: str, category: str | None, cats: list[str]) -> None:
    with st.container(border=True):
        st.subheader(f"Edit: {slug}")

        with st.form(key=f"edit_{tag_id}"):
            new_slug = st.text_input("slug", value=slug)
            new_name = st.text_input("name", value=name)
            cat_opts  = [""] + cats
            cat_idx   = cat_opts.index(category) if category in cat_opts else 0
            new_cat   = st.selectbox("category", cat_opts, index=cat_idx)

            if new_slug != slug:
                st.warning("Changing slug changes the public URL.")

            if st.form_submit_button("Save"):
                ns = new_slug.strip()
                if not _SLUG_RE.match(ns):
                    st.error("slug must be lowercase, digits, hyphens only")
                else:
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE cat.tags SET slug=%s, name=%s, category=%s WHERE id=%s",
                            (ns, new_name.strip() or ns.replace("-", " ").title(),
                             new_cat or None, tag_id),
                        )
                        conn.commit()
                    st.success("Saved.")
                    st.rerun()

        with get_conn() as conn:
            aliases = conn.execute(queries.TAG_ALIASES, (tag_id,)).fetchall()
            samples = conn.execute(queries.TAG_SAMPLE_VIDEOS, (tag_id,)).fetchall()

        st.write("**Aliases**")
        for normalized, source, confidence in aliases:
            st.write(f"  `{normalized}`  — {source}  ({confidence:.0%})")

        if samples:
            st.write("**Sample videos**")
            for vid_id, title in samples:
                st.write(f"  #{vid_id}: {title or '—'}")
