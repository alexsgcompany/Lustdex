import streamlit as st

from page_screens import queries
from pipeline.common.db import get_conn
from pipeline.pages.cooccurrence import THRESHOLD, additions_for
from pipeline.pages.slug import load_tag_slugs, provisional_slug


def render() -> None:
    st.header("Page Builder")

    with get_conn() as conn:
        all_tags = conn.execute(
            "SELECT id, name, slug FROM cat.tags ORDER BY name"
        ).fetchall()
        projections = conn.execute(
            "SELECT id, slug, name, brand_tag_id FROM cat.projections"
            " WHERE active ORDER BY slug"
        ).fetchall()

    tag_name  = {t[0]: t[1] for t in all_tags}
    tag_label = {t[0]: f"{t[1]}  ({t[2]})" for t in all_tags}

    # ── projection selector ───────────────────────────────────────────────────
    proj_by_id = {p[0]: p for p in projections}
    proj_choice = st.selectbox(
        "Projection",
        options=[None, *proj_by_id.keys()],
        format_func=lambda x: "— all (no projection) —" if x is None
                              else f"{proj_by_id[x][1]} ({proj_by_id[x][2]})",
    )

    pinned_id: int | None = proj_by_id[proj_choice][3] if proj_choice else None
    if proj_choice is not None and pinned_id is None:
        st.caption(
            f"Projection **{proj_by_id[proj_choice][1]}** has no brand tag — "
            f"candidates will be unfiltered (served on this projection if no "
            f"brand_tag is set; see spec 07 §5.3)."
        )

    # ── base tag selection (pinned brand_tag + user-picked extras) ────────────
    if pinned_id is not None:
        st.caption(f"Brand tag pinned from projection: **{tag_name[pinned_id]}**")

    extras: list[int] = st.multiselect(
        "Additional base tags" if pinned_id is not None else "Base tags",
        options=[t[0] for t in all_tags if t[0] != pinned_id],
        format_func=lambda x: tag_label[x],
    )

    base_ids: list[int] = ([pinned_id] if pinned_id is not None else []) + extras

    if not base_ids:
        st.info("Select a projection with a brand tag, or pick base tag(s), "
                "to see addition candidates.")
        return

    # ── fetch all data in one connection block ────────────────────────────────
    with get_conn() as conn:
        additions = additions_for(conn, base_ids)

        existing_sets: set[tuple[int, ...]] = {
            tuple(r[0])
            for r in conn.execute(queries.EXISTING_FOR_BASE, (base_ids,)).fetchall()
        }

        if additions:
            needed_ids = list({*base_ids, *(a[0] for a in additions)})
            t_info = load_tag_slugs(conn, needed_ids)
        else:
            t_info = {}

    # ── render ────────────────────────────────────────────────────────────────
    base_names = ", ".join(tag_name[i] for i in base_ids)

    if not additions:
        st.info(
            f"No addition candidates for **{base_names}** "
            f"(threshold: ≥ {THRESHOLD} co-occurrences)."
        )
        return

    already = sum(
        1 for a in additions
        if tuple(sorted(base_ids + [a[0]])) in existing_sets
    )
    st.write(
        f"**{len(additions)}** candidates for base: **{base_names}**"
        + (f"  ·  {already} already approved" if already else "")
    )

    # key suffix that resets slug edits whenever the base selection changes
    base_key = "_".join(str(i) for i in base_ids)

    # column headers
    hc = st.columns([3, 2, 1, 5, 1])
    for col, label in zip(hc, ["Tag", "Category", "Count", "Slug", "✓"]):
        col.markdown(f"**{label}**")

    selected: list[tuple[int, list[int], int, str]] = []

    for tag_id, name, category, count in additions:
        member_ids   = sorted(base_ids + [tag_id])
        ordered_ids  = list(base_ids) + [tag_id]
        default_slug = provisional_slug(t_info, ordered_ids)
        is_done      = tuple(member_ids) in existing_sets

        row = st.columns([3, 2, 1, 5, 1])
        row[0].write(name)
        row[1].write(category or "—")
        row[2].write(count)

        if is_done:
            row[3].markdown(f"`{default_slug}`")
            if row[4].button(
                "🗑️",
                key=f"del_{base_key}_{tag_id}",
                help="Remove this combination",
            ):
                with get_conn() as conn:
                    conn.execute(queries.DELETE_CANDIDATE, (member_ids,))
                    conn.commit()
                st.success(
                    "Removed: "
                    + ", ".join(tag_name[i] for i in member_ids)
                )
                st.rerun()
        else:
            edited_slug = row[3].text_input(
                "", value=default_slug,
                key=f"slug_{base_key}_{tag_id}",
                label_visibility="collapsed",
            )
            if row[4].checkbox("", key=f"cb_{tag_id}", label_visibility="collapsed"):
                selected.append((tag_id, member_ids, count, edited_slug))

    if not selected:
        return

    st.markdown("---")
    if st.button(f"Approve {len(selected)} candidate(s)", type="primary"):
        with get_conn() as conn:
            approved = 0
            for _, member_ids, count, slug in selected:
                result = conn.execute(
                    queries.INSERT_CANDIDATE, (member_ids, count, slug)
                ).fetchone()
                if result:
                    approved += 1
            conn.commit()

        for tag_id, _, _, _ in selected:
            st.session_state.pop(f"cb_{tag_id}", None)

        st.success(f"Approved {approved} new candidate(s).")
        st.rerun()
