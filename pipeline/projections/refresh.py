"""Rebuild read-path rollups via shadow-table + atomic swap. See specs/13-read-path-rollups.md.

Pre-resolves projection membership and per-tag / per-page counts so cold-cache
public-site routes stop re-scanning the whole vertical on every hit.

Each rollup is loaded into a `*_new` shadow table the live site does NOT read,
indexed there, then atomically renamed over the live table in one transaction —
so the live table is never empty, index-less, or partially loaded under reads
(spec 13 §4/R1). Strict order: membership (projection_videos) swaps FIRST, then the
count rollups read the now-fresh live membership.

Idempotent: re-running produces identical tables. Run after promote.videos + the
tags cascade, before deploy/data-sync.

Usage:
    python -m pipeline.projections.refresh
"""

import pipeline.common.config  # noqa: F401 — ensures .env is loaded
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger("projections.refresh")

# --- rebuild SELECTs ---

# Membership rule, byte-identical to spec 06 §4: (vertical OR include) AND NOT exclude.
# published_at denormalized for the ordered listing index.
MEMBERSHIP_SQL = """
INSERT INTO cat.projection_videos_new (projection_id, video_id, published_at)
SELECT p.id, v.id, v.published_at
FROM cat.projections p
JOIN cat.videos v ON (
        v.vertical = ANY(p.from_verticals)
     OR EXISTS (SELECT 1 FROM cat.video_tags vt
                WHERE vt.video_id = v.id AND vt.tag_id = ANY(p.include_tag_ids))
)
WHERE p.active
  AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                  WHERE vt.video_id = v.id AND vt.tag_id = ANY(p.exclude_tag_ids))
"""

# Counts read the already-swapped fresh live cat.projection_videos.
TAG_COUNTS_SQL = """
INSERT INTO cat.tag_counts_new (projection_id, tag_id, n)
SELECT pv.projection_id, vt.tag_id, count(*)
FROM cat.projection_videos pv
JOIN cat.video_tags vt ON vt.video_id = pv.video_id
GROUP BY pv.projection_id, vt.tag_id
"""

# Videos in a projection that carry ALL of a page's member tags (spec 06 §5 / P11).
# Only emits n > 0 rows by construction.
PAGE_COUNTS_SQL = """
INSERT INTO cat.page_counts_new (projection_id, page_id, n)
WITH page_video AS (
    SELECT pc.id AS page_id, vt.video_id
    FROM cat.page_candidates pc
    JOIN cat.video_tags vt ON vt.tag_id = ANY(pc.member_tag_ids)
    GROUP BY pc.id, vt.video_id
    HAVING count(DISTINCT vt.tag_id) = cardinality(pc.member_tag_ids)
)
SELECT pv.projection_id, pvd.page_id, count(*)
FROM page_video pvd
JOIN cat.projection_videos pv ON pv.video_id = pvd.video_id
GROUP BY pv.projection_id, pvd.page_id
"""


def rebuild(conn, table: str, insert_sql: str, index_ddls: list[tuple[str, str]]) -> None:
    """Load cat.{table}_new, index it, then atomically swap it over cat.{table}.

    index_ddls is a list of (shadow_index_name, create_ddl). After the swap each
    shadow index is renamed to its canonical name (shadow name minus '_new'), which
    the dropped old table just freed.
    """
    new = f"{table}_new"
    log.info("rebuild cat.%s", table)
    conn.execute(f"DROP TABLE IF EXISTS cat.{new}")
    conn.execute(f"CREATE TABLE cat.{new} (LIKE cat.{table} INCLUDING DEFAULTS)")
    n = conn.execute(insert_sql).rowcount
    log.info("  loaded %d rows", n)
    for _, ddl in index_ddls:
        conn.execute(ddl)
    conn.commit()  # persist the built shadow before the swap

    with conn.transaction():  # atomic publish: data + index together
        conn.execute(f"DROP TABLE cat.{table}")  # frees the canonical index names
        conn.execute(f"ALTER TABLE cat.{new} RENAME TO {table}")
        for name, _ in index_ddls:
            conn.execute(f"ALTER INDEX cat.{name} RENAME TO {name.replace('_new', '', 1)}")
    conn.commit()
    log.info("  swapped cat.%s (%d rows)", table, n)


def run() -> int:
    with get_conn() as conn:
        # 1. membership first — the counts depend on the fresh live table.
        rebuild(conn, "projection_videos", MEMBERSHIP_SQL, [
            ("projection_videos_new_pkey",
             "ALTER TABLE cat.projection_videos_new "
             "ADD CONSTRAINT projection_videos_new_pkey PRIMARY KEY (projection_id, video_id)"),
            ("projection_videos_new_listing",
             "CREATE INDEX projection_videos_new_listing ON cat.projection_videos_new "
             "(projection_id, published_at DESC NULLS LAST, video_id DESC)"),
        ])
        # 2. counts over the now-fresh membership.
        rebuild(conn, "tag_counts", TAG_COUNTS_SQL, [
            ("tag_counts_new_pkey",
             "ALTER TABLE cat.tag_counts_new "
             "ADD CONSTRAINT tag_counts_new_pkey PRIMARY KEY (projection_id, tag_id)"),
        ])
        rebuild(conn, "page_counts", PAGE_COUNTS_SQL, [
            ("page_counts_new_pkey",
             "ALTER TABLE cat.page_counts_new "
             "ADD CONSTRAINT page_counts_new_pkey PRIMARY KEY (projection_id, page_id)"),
        ])
    log.info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
