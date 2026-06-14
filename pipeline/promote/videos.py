"""Promote raw.raw_videos rows into cat.videos (lean serving projection).

Idempotent:
  - first INSERT: sets slug + go_token (PINNED — never changed afterwards)
  - subsequent runs: refresh title/duration/target_url/published_at only

`has_thumb` / `previews_count` are owned by pipeline.media.* and left untouched here.

Usage:
    python -m pipeline.promote.videos
"""

import re
import secrets
import unicodedata

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger("promote.videos")

SLUG_MAX = 60


def _slug(title: str | None) -> str:
    if not title:
        return "v"
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        return "v"
    if len(s) > SLUG_MAX:
        head = s[:SLUG_MAX]
        cut = head.rsplit("-", 1)[0]
        s = cut if cut else head
    return s


_UPSERT_SQL = """
INSERT INTO cat.videos (
    id, provider_id, external_id, title, slug, go_token,
    duration_sec, target_url, published_at, vertical
) VALUES (
    %(id)s, %(provider_id)s, %(external_id)s, %(title)s, %(slug)s, %(go_token)s,
    %(duration_sec)s, %(target_url)s, %(published_at)s, %(vertical)s
)
ON CONFLICT (id) DO UPDATE SET
    title        = EXCLUDED.title,
    duration_sec = EXCLUDED.duration_sec,
    target_url   = EXCLUDED.target_url,
    published_at = EXCLUDED.published_at,
    vertical     = EXCLUDED.vertical
    -- slug and go_token are NOT updated: pinned at first promote.
    -- vertical IS refreshed: reflects current feed config (see specs/06-projections.md P4).
"""


def run() -> int:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT rv.id, rv.provider_id, rv.external_id, rv.title,
                   rv.duration_sec, rv.target_url, rv.published_at, f.niche
            FROM raw.raw_videos rv
            JOIN raw.feeds f ON f.id = rv.feed_id
            LEFT JOIN cat.videos v ON v.id = rv.id
            WHERE v.id IS NULL
            ORDER BY rv.id
            """
        ).fetchall()
        log.info("pending promote: %d rows", len(rows))
        if not rows:
            return 0

        batch = [
            {
                "id": r[0],
                "provider_id": r[1],
                "external_id": r[2],
                "title": r[3],
                "slug": _slug(r[3]),
                "go_token": secrets.token_hex(8),
                "duration_sec": r[4],
                "target_url": r[5] or "",
                "published_at": r[6],
                "vertical": r[7],
            }
            for r in rows
        ]
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, batch)
        conn.commit()
        log.info("promoted: %d rows", len(batch))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
