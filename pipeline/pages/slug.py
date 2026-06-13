"""Provisional slug generation for page candidates."""


def load_tag_slugs(conn, tag_ids: list[int]) -> dict[int, str]:
    """Return {tag_id: slug} for the given tag_ids."""
    rows = conn.execute(
        "SELECT id, slug FROM cat.tags WHERE id = ANY(%s)",
        (tag_ids,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def provisional_slug(tag_slugs: dict[int, str], ordered_tag_ids: list[int]) -> str:
    """Build slug from tag slugs in the given order, joined by '-'.
    Caller controls order: typically base tags (in selection order) + addition."""
    return "-".join(tag_slugs[tid] for tid in ordered_tag_ids)
