"""Co-occurrence query: given a base set of tag_ids, return addition candidates."""

THRESHOLD = 20  # minimum shared videos to include an addition


def additions_for(conn, base_tag_ids: list[int]) -> list[tuple[int, str, str | None, int]]:
    """Return (tag_id, name, category, count) for canonical tags that co-occur with
    ALL base tags in at least THRESHOLD videos, ordered by count DESC."""
    if not base_tag_ids:
        return []
    rows = conn.execute(
        """
        WITH base_videos AS (
            SELECT video_id
            FROM video_tags
            WHERE tag_id = ANY(%s)
            GROUP BY video_id
            HAVING COUNT(DISTINCT tag_id) = %s
        )
        SELECT t.id, t.name, t.category, COUNT(*) AS cnt
        FROM video_tags vt
        JOIN base_videos bv ON bv.video_id = vt.video_id
        JOIN tags t ON t.id = vt.tag_id
        WHERE vt.tag_id != ALL(%s)
        GROUP BY t.id, t.name, t.category
        HAVING COUNT(*) >= %s
        ORDER BY cnt DESC
        """,
        (base_tag_ids, len(base_tag_ids), base_tag_ids, THRESHOLD),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]
