"""Semantic landing pages screen (spec 10).

Create / refresh / approve vector-driven landing pages. The encoder is loaded
once per session (st.cache_resource) and reused for every snapshot; snapshots
run inline because this is a low-frequency, single-operator action.
"""

import psycopg
import streamlit as st

from pipeline.common.db import get_conn
from pipeline.semantic_pages.create import INSERT_SQL, SOURCES, kebab
from pipeline.semantic_pages.encode import load_model, store_vec
from pipeline.semantic_pages.refresh import (
    DEFAULT_MODEL,
    EF_SEARCH,
    MAX_DIST,
    STALE_DAYS,
    TOP_K,
    snapshot_one,
)

_STATUS_ICON = {"draft": "📝", "approved": "✅", "rejected": "🚫"}

LIST_SQL = """
SELECT id,
       COALESCE(slug_final, slug_provisional) AS slug,
       query_text, source, source_volume, status,
       COALESCE(array_length(video_ids, 1), 0) AS n_vids,
       refreshed_at, embedding_model,
       (embedding_model != %(model)s
        OR refreshed_at < now() - make_interval(days => %(days)s)) AS stale,
       query_vec IS NULL AS needs_encode
FROM cat.semantic_pages
{where}
ORDER BY created_at DESC
"""


@st.cache_resource(show_spinner="Loading encoder…")
def _model():
    return load_model(DEFAULT_MODEL, "auto")


def _encode_and_snapshot(row_id: int) -> None:
    """New row: encode query_vec (needs model), then snapshot (pure SQL)."""
    with st.spinner("Encoding + snapshotting…"), get_conn() as conn:
        store_vec(conn, row_id, _model(), DEFAULT_MODEL)
        snapshot_one(conn, row_id, TOP_K, MAX_DIST, EF_SEARCH)


def _snapshot(row_id: int) -> None:
    """Refresh: pure-SQL re-snapshot from the stored query_vec, no model."""
    with st.spinner("Snapshotting…"), get_conn() as conn:
        snapshot_one(conn, row_id, TOP_K, MAX_DIST, EF_SEARCH)


def _set_status(row_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE cat.semantic_pages SET status = %s WHERE id = %s", (status, row_id)
        )
        conn.commit()


def render() -> None:
    st.header("Semantic Pages")
    st.caption(
        "Vector-driven landing pages (spec 10). Each row freezes a top-K "
        f"snapshot (K={TOP_K}, max_dist={MAX_DIST}) of similar videos for a "
        "query. Only **approved** rows render on the site."
    )

    # ── create ────────────────────────────────────────────────────────────────
    with st.form("create_semantic", clear_on_submit=True):
        query = st.text_input("Query text")
        c1, c2 = st.columns(2)
        source = c1.selectbox("Source", SOURCES)
        volume = c2.number_input("Source volume", min_value=0, value=0, step=10)
        aliases_raw = st.text_area("Aliases (one per line)", height=80)
        submitted = st.form_submit_button("Create + snapshot", type="primary")

    if submitted:
        q = query.strip()
        if not q:
            st.error("Query text is required.")
        else:
            aliases = [a.strip() for a in aliases_raw.splitlines() if a.strip()]
            slug = kebab(q)
            try:
                with get_conn() as conn:
                    rid = conn.execute(
                        INSERT_SQL,
                        (q, aliases, source, volume or None, slug,
                         TOP_K, MAX_DIST, DEFAULT_MODEL),
                    ).fetchone()[0]
                    conn.commit()
                _encode_and_snapshot(rid)
                st.success(f"Created id={rid} slug={slug} (draft). Approve to publish.")
                st.rerun()
            except psycopg.errors.UniqueViolation:
                st.error(f"Query already exists: {q!r} (merge via aliases manually).")

    st.divider()

    # ── list ──────────────────────────────────────────────────────────────────
    flt = st.radio("Show", ["all", "draft", "approved", "rejected"], horizontal=True)
    where = "" if flt == "all" else "WHERE status = %(status)s"
    params = {"model": DEFAULT_MODEL, "days": STALE_DAYS}
    if flt != "all":
        params["status"] = flt

    with get_conn() as conn:
        rows = conn.execute(LIST_SQL.format(where=where), params).fetchall()

    if not rows:
        st.info("No semantic pages yet.")
        return

    for (rid, slug, query_text, src, vol, status, n_vids,
         refreshed_at, model, stale, needs_encode) in rows:
        icon = _STATUS_ICON.get(status, "❓")
        ts = refreshed_at.strftime("%Y-%m-%d %H:%M") if refreshed_at else "—"
        stale_tag = " ⚠️ stale" if stale else ""
        encode_tag = " 🔑 needs encode" if needs_encode else ""
        header = (f"{icon} **{query_text}**  ·  `/s/{slug}`  ·  "
                  f"{n_vids} videos  ·  {src}"
                  + (f" ({vol})" if vol else "") + stale_tag + encode_tag)

        with st.expander(header):
            st.caption(f"id={rid}  ·  refreshed {ts}  ·  model {model}")
            cols = st.columns(4)
            if status != "approved" and cols[0].button(
                "Approve", key=f"appr_{rid}", type="primary"):
                _set_status(rid, "approved")
                st.rerun()
            if status != "rejected" and cols[1].button("Reject", key=f"rej_{rid}"):
                _set_status(rid, "rejected")
                st.rerun()
            if cols[2].button("Refresh", key=f"refr_{rid}"):
                # Legacy rows (no query_vec) need an encode pass first.
                (_encode_and_snapshot if needs_encode else _snapshot)(rid)
                st.rerun()
            if cols[3].button("Delete", key=f"del_{rid}"):
                with get_conn() as conn:
                    conn.execute("DELETE FROM cat.semantic_pages WHERE id = %s", (rid,))
                    conn.commit()
                st.rerun()
