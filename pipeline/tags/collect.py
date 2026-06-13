"""Scan raw.raw_videos.tags_raw, normalize every tag, populate raw.unmapped_tags.

Run:  python -m pipeline.tags.collect
"""

import unicodedata
from collections import defaultdict

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.common.runs import pipeline_run
from pipeline.tags.normalize import TRASH, normalize

log = get_logger(__name__)
JOB = "tags.collect"

_MAX_EXAMPLES = 5

# Recount is idempotent: upsert replaces freq/providers/raw_examples each run.
_UPSERT_PENDING = """
INSERT INTO raw.unmapped_tags (normalized, raw_examples, freq, providers)
VALUES (%s, %s, %s, %s)
ON CONFLICT (normalized) DO UPDATE SET
    freq         = EXCLUDED.freq,
    raw_examples = EXCLUDED.raw_examples,
    providers    = EXCLUDED.providers,
    -- re-open entries that were trashed by an old stoplist but are now pending
    status       = CASE WHEN raw.unmapped_tags.status = 'trash' THEN 'pending'
                        ELSE raw.unmapped_tags.status END,
    updated_at   = now()
"""

_UPSERT_TRASH = """
INSERT INTO raw.unmapped_tags (normalized, raw_examples, freq, status, providers)
VALUES (%s, %s, %s, 'trash', %s)
ON CONFLICT (normalized) DO UPDATE SET
    freq         = EXCLUDED.freq,
    raw_examples = EXCLUDED.raw_examples,
    providers    = EXCLUDED.providers,
    updated_at   = now()
"""


def _raw_key(raw: str) -> str:
    """Step-1 key used as PK for trash entries (no letter requirement)."""
    return unicodedata.normalize("NFKC", raw).lower().strip()[:200]


def main() -> None:
    with pipeline_run(JOB) as stats:
        with get_conn() as conn:
            _run(conn, stats)


def _run(conn, stats: dict) -> None:
    aliased: set[str] = {
        row[0] for row in conn.execute("SELECT normalized FROM cat.tag_aliases")
    }

    rows = conn.execute("SELECT tags_raw, provider_id FROM raw.raw_videos").fetchall()

    # Accumulate counts keyed by normalized form
    pending_freq:     dict[str, int]        = defaultdict(int)
    pending_examples: dict[str, list[str]]  = defaultdict(list)
    pending_providers: dict[str, set[int]]  = defaultdict(set)
    trash_freq:       dict[str, int]        = defaultdict(int)
    trash_examples:   dict[str, list[str]]  = defaultdict(list)
    trash_providers:  dict[str, set[int]]   = defaultdict(set)

    raw_tags_seen = 0
    aliased_hits = 0

    for tags_raw, provider_id in rows:
        for raw in tags_raw:
            raw_tags_seen += 1
            key = normalize(raw)
            if key == TRASH:
                tk = _raw_key(raw)
                trash_freq[tk] += 1
                if raw not in trash_examples[tk] and len(trash_examples[tk]) < _MAX_EXAMPLES:
                    trash_examples[tk].append(raw)
                trash_providers[tk].add(provider_id)
            elif key in aliased:
                aliased_hits += 1
            else:
                pending_freq[key] += 1
                if raw not in pending_examples[key] and len(pending_examples[key]) < _MAX_EXAMPLES:
                    pending_examples[key].append(raw)
                pending_providers[key].add(provider_id)

    # Upsert pending
    pending_rows = [
        (k, pending_examples[k], pending_freq[k], list(pending_providers[k]))
        for k in pending_freq
    ]
    trash_rows = [
        (k, trash_examples[k], trash_freq[k], list(trash_providers[k]))
        for k in trash_freq
    ]

    with conn.cursor() as cur:
        if pending_rows:
            cur.executemany(_UPSERT_PENDING, pending_rows)
        if trash_rows:
            cur.executemany(_UPSERT_TRASH, trash_rows)
    conn.commit()

    stats.update(
        raw_tags_seen=raw_tags_seen,
        unique_keys=len(pending_freq),
        aliased_hits=aliased_hits,
        new_pending=len(pending_rows),
        trash=len(trash_rows),
    )
    log.info(
        "raw_tags_seen=%d  unique_keys=%d  aliased_hits=%d  trash=%d",
        raw_tags_seen, len(pending_freq), aliased_hits, len(trash_rows),
    )


if __name__ == "__main__":
    main()
