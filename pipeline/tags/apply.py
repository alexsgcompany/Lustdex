"""Walk raw.raw_videos.tags_raw → normalize → cat.tag_aliases lookup → cat.video_tags.

Run: python -m pipeline.tags.apply
Idempotent: ON CONFLICT DO NOTHING, so re-runs safely add new links
when the alias map grows (e.g. after review-queue imports).
"""

import pipeline.common.config  # noqa: F401  (loads .env)
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.common.runs import pipeline_run
from pipeline.tags.normalize import TRASH, normalize

log = get_logger(__name__)


def main() -> None:
    with pipeline_run("tags.apply") as stats:
        with get_conn() as conn:
            alias_map: dict[str, int] = {
                row[0]: row[1]
                for row in conn.execute("SELECT normalized, tag_id FROM cat.tag_aliases").fetchall()
            }

            videos = conn.execute(
                "SELECT id, tags_raw FROM raw.raw_videos WHERE tags_raw IS NOT NULL"
            ).fetchall()

            pairs: list[tuple[int, int]] = []
            tags_trash = tags_missing = 0

            for video_id, tags_raw in videos:
                for raw_tag in (tags_raw or []):
                    key = normalize(raw_tag)
                    if key == TRASH:
                        tags_trash += 1
                        continue
                    tag_id = alias_map.get(key)
                    if tag_id is None:
                        tags_missing += 1
                        continue
                    pairs.append((video_id, tag_id))

            if pairs:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO cat.video_tags (video_id, tag_id) VALUES (%s, %s)"
                        " ON CONFLICT DO NOTHING",
                        pairs,
                    )
            conn.commit()

        stats.update(
            videos=len(videos),
            tags_trash=tags_trash,
            tags_missing=tags_missing,
            pairs_written=len(pairs),
        )
        log.info(
            "videos=%d  trash=%d  missing=%d  pairs=%d",
            len(videos), tags_trash, tags_missing, len(pairs),
        )


if __name__ == "__main__":
    main()
