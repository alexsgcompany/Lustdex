"""Scan raw performers, promote frequent names to cat.performers + aliases.

Run:  python -m pipeline.performers.canonicalize [--min-videos 100]

Single pass over raw.raw_videos.performers_raw (spec 11 §4a). normalize() each
name to a merge key, count occurrences, vote the canonical display name (most
frequent surface form, ties → longest). Keys with freq >= MIN_VIDEOS become
cat.performers rows; every contributing key becomes a cat.performer_aliases row
pointing at it. Idempotent: ON CONFLICT upserts, re-runs converge.
"""

import argparse
import re
import unicodedata
from collections import Counter, defaultdict

import pipeline.common.config  # noqa: F401  (loads .env)
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.common.runs import pipeline_run
from pipeline.performers.normalize import normalize

log = get_logger(__name__)
JOB = "performers.canonicalize"
DEFAULT_MIN_VIDEOS = 100
SLUG_MAX = 60

_UPSERT_PERFORMER = """
INSERT INTO cat.performers (slug, name)
VALUES (%s, %s)
ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, updated_at = now()
"""

_UPSERT_ALIAS = """
INSERT INTO cat.performer_aliases (normalized, performer_id, source)
VALUES (%s, %s, 'collect')
ON CONFLICT (normalized) DO UPDATE SET performer_id = EXCLUDED.performer_id
"""


def slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        return "p"
    if len(s) > SLUG_MAX:
        head = s[:SLUG_MAX]
        s = head.rsplit("-", 1)[0] or head
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-videos", type=int, default=DEFAULT_MIN_VIDEOS, dest="min_videos")
    args = ap.parse_args()
    with pipeline_run(JOB) as stats:
        with get_conn() as conn:
            _run(conn, stats, args.min_videos)


def _run(conn, stats: dict, min_videos: int) -> None:
    freq: dict[str, int] = defaultdict(int)
    surfaces: dict[str, Counter] = defaultdict(Counter)
    names_seen = skipped = 0

    rows = conn.execute("SELECT performers_raw FROM raw.raw_videos").fetchall()
    for (performers_raw,) in rows:
        for name in performers_raw:
            names_seen += 1
            key = normalize(name)
            if not key:
                skipped += 1
                continue
            freq[key] += 1
            surfaces[key][name.strip()] += 1

    # Promote keys at/above the floor. Canonical display = most frequent surface
    # form; ties broken by longest (spec 11 §P6).
    promoted = {k: f for k, f in freq.items() if f >= min_videos}

    def canonical(key: str) -> str:
        return max(surfaces[key].items(), key=lambda kv: (kv[1], len(kv[0])))[0]

    # slug per key (two keys may collide on slug → they share one performer row).
    perf_rows = [(slugify(canonical(k)), canonical(k)) for k in promoted]

    with conn.cursor() as cur:
        cur.executemany(_UPSERT_PERFORMER, perf_rows)
    conn.commit()

    slug_to_id = {
        row[0]: row[1]
        for row in conn.execute("SELECT slug, id FROM cat.performers").fetchall()
    }
    alias_rows = [(k, slug_to_id[slugify(canonical(k))]) for k in promoted]
    with conn.cursor() as cur:
        cur.executemany(_UPSERT_ALIAS, alias_rows)
    conn.commit()

    stats.update(
        names_seen=names_seen,
        skipped=skipped,
        distinct_keys=len(freq),
        promoted=len(promoted),
        min_videos=min_videos,
    )
    log.info(
        "names_seen=%d  skipped=%d  distinct_keys=%d  promoted=%d (min_videos=%d)",
        names_seen, skipped, len(freq), len(promoted), min_videos,
    )


if __name__ == "__main__":
    main()
