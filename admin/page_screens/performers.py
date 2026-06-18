"""Performers screen (spec 11 §5).

Review canonicalized performers and override gender. Admin edits set
gender_source='admin' (confidence 1.0) and always win over the LLM pass.
Hiding flips status='hidden' (noise the floor + LLM missed). No alias-merge UI
in v1.
"""

import streamlit as st

from pipeline.common.db import get_conn

GENDERS = ("unknown", "female", "trans", "male")

LIST_SQL = """
SELECT id, slug, name, gender, gender_source, gender_confidence, status, n_videos
FROM cat.performers
{where}
ORDER BY n_videos DESC
LIMIT 300
"""

_STATUS_ICON = {"active": "✅", "hidden": "🚫"}
_GENDER_ICON = {"female": "♀", "male": "♂", "trans": "⚧", "unknown": "❓"}


def _set_gender(pid: int, gender: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE cat.performers SET gender=%s, gender_source='admin',"
            " gender_confidence=1.0, updated_at=now() WHERE id=%s",
            (gender, pid),
        )
        conn.commit()


def _set_status(pid: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE cat.performers SET status=%s, updated_at=now() WHERE id=%s",
            (status, pid),
        )
        conn.commit()


def render() -> None:
    st.header("Performers")
    st.caption(
        "Canonicalized performers (spec 11). Gender is LLM-classified; edits "
        "here override it (`gender_source='admin'`). Hide non-person noise."
    )

    c1, c2 = st.columns(2)
    g_flt = c1.radio("Gender", ["all", *GENDERS], horizontal=True)
    s_flt = c2.radio("Status", ["all", "active", "hidden"], horizontal=True)

    clauses, params = [], {}
    if g_flt != "all":
        clauses.append("gender = %(g)s")
        params["g"] = g_flt
    if s_flt != "all":
        clauses.append("status = %(s)s")
        params["s"] = s_flt
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_conn() as conn:
        rows = conn.execute(LIST_SQL.format(where=where), params).fetchall()

    if not rows:
        st.info("No performers match.")
        return

    st.caption(f"{len(rows)} shown (top 300 by video count).")
    for pid, slug, name, gender, src, conf, status, n_vids in rows:
        sicon = _STATUS_ICON.get(status, "")
        gicon = _GENDER_ICON.get(gender, "")
        if src:
            src_tag = f" · {src}" + (f" {conf:.2f}" if conf is not None else "")
        else:
            src_tag = " · unclassified"
        header = f"{sicon} {gicon} **{name}** · `/actor/{slug}` · {n_vids} videos{src_tag}"

        with st.expander(header):
            cols = st.columns([2, 1, 1])
            new_g = cols[0].selectbox(
                "Gender", GENDERS, index=GENDERS.index(gender), key=f"g_{pid}"
            )
            if cols[0].button("Save gender", key=f"sg_{pid}", type="primary"):
                _set_gender(pid, new_g)
                st.rerun()
            if status == "active" and cols[1].button("Hide", key=f"h_{pid}"):
                _set_status(pid, "hidden")
                st.rerun()
            if status == "hidden" and cols[1].button("Unhide", key=f"u_{pid}"):
                _set_status(pid, "active")
                st.rerun()
