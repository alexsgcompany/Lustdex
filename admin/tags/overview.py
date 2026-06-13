import streamlit as st
from pipeline.common.db import get_conn
from tags import queries


def render() -> None:
    st.header("Overview")
    if st.button("Refresh"):
        st.rerun()

    with get_conn() as conn:
        cov_row   = conn.execute(queries.COVERAGE).fetchone()
        cnt_row   = conn.execute(queries.COUNTERS).fetchone()
        orphan_n  = conn.execute(queries.ORPHAN_COUNT).fetchone()[0]
        cat_rows  = conn.execute(queries.CATEGORY_DISTRIBUTION).fetchall()

    total_occ, resolved_occ = int(cov_row[0]), int(cov_row[1])
    pct = resolved_occ / total_occ * 100 if total_occ else 0.0

    canonical_total, aliases_total, queue_pending, queue_trash = cnt_row

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Coverage", f"{pct:.1f}%",
              help=f"{resolved_occ:,} of {total_occ:,} tag occurrences resolve to a canonical tag")
    c2.metric("Canonical tags",   canonical_total)
    c3.metric("Aliases",          aliases_total)
    c4.metric("Queue (pending)",  queue_pending)
    c5.metric("Trash",            queue_trash)

    st.divider()

    st.subheader(f"Orphan videos: {orphan_n}")
    if orphan_n:
        with st.expander("Show orphan videos"):
            with get_conn() as conn:
                orphans = conn.execute(queries.ORPHAN_LIST).fetchall()
            for vid_id, title, tags_raw in orphans:
                raw_str = ", ".join(tags_raw or [])[:120]
                st.write(f"**#{vid_id}** {title or '—'}  — _{raw_str}_")

    st.divider()

    st.subheader("Canonical tags by category")
    st.table({"Category": [r[0] for r in cat_rows], "Count": [r[1] for r in cat_rows]})
