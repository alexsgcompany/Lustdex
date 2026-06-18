"""Fill cat.video_performers from raw names via the alias map; refresh n_videos.

Run:  python -m pipeline.performers.apply

Mirrors pipeline.tags.apply: normalize() each raw name, look it up in
cat.performer_aliases, emit (video_id, performer_id). video_id == raw_videos.id
(shared id-space, spec 11 §P10 / migration 007). Idempotent: ON CONFLICT DO
NOTHING, then n_videos is recomputed from the junction.
"""

import pipeline.common.config  # noqa: F401  (loads .env)
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.common.runs import pipeline_run
from pipeline.performers.normalize import normalize

log = get_logger(__name__)
JOB = "performers.apply"

_REFRESH_COUNTS = """
UPDATE cat.performers p
SET n_videos = COALESCE(c.n, 0), updated_at = now()
FROM (SELECT id FROM cat.performers) ids
LEFT JOIN (
    SELECT performer_id, count(*) AS n
    FROM cat.video_performers GROUP BY performer_id
) c ON c.performer_id = ids.id
WHERE p.id = ids.id
"""


def main() -> None:
    with pipeline_run(JOB) as stats:
        with get_conn() as conn:
            _run(conn, stats)


def _run(conn, stats: dict) -> None:
    alias_map: dict[str, int] = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT normalized, performer_id FROM cat.performer_aliases"
        ).fetchall()
    }

    videos = conn.execute(
        "SELECT id, performers_raw FROM raw.raw_videos WHERE performers_raw <> '{}'"
    ).fetchall()

    pairs: list[tuple[int, int]] = []
    missing = 0
    for video_id, performers_raw in videos:
        for name in performers_raw:
            key = normalize(name)
            if not key:
                continue
            pid = alias_map.get(key)
            if pid is None:
                missing += 1
                continue
            pairs.append((video_id, pid))

    if pairs:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO cat.video_performers (video_id, performer_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                pairs,
            )
    conn.execute(_REFRESH_COUNTS)
    conn.commit()

    stats.update(
        videos=len(videos),
        pairs_written=len(pairs),
        missing=missing,
    )
    log.info(
        "videos=%d  pairs=%d  missing=%d (names below floor / not promoted)",
        len(videos), len(pairs), missing,
    )


if __name__ == "__main__":
    main()
