"""Cascade tag resolution: L1 (exact) then L2 (fuzzy) over raw.unmapped_tags.

Run:  python -m pipeline.tags.cascade
"""

from rapidfuzz import fuzz

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.common.runs import pipeline_run
from pipeline.tags.normalize import TRASH, normalize

log = get_logger(__name__)
JOB = "tags.cascade"

_FUZZY_AUTO   = 94.0   # score ≥ this → auto-resolve
_FUZZY_REVIEW = 86.0   # score ≥ this → suggestion


def _collision_guard(a: str, b: str) -> bool:
    """Return True if this pair should NOT be auto-mapped (teen/ten-class risk)."""
    if not a or not b:
        return True
    a_t, b_t = a.split(), b.split()
    a_short = len(a_t) == 1 and len(a_t[0]) <= 4
    b_short = len(b_t) == 1 and len(b_t[0]) <= 4
    return (a_short or b_short) and a[0] != b[0]


def main() -> None:
    with pipeline_run(JOB) as stats:
        with get_conn() as conn:
            _run(conn, stats)


def _run(conn, stats: dict) -> None:
    # Candidate pool: canonical tags + existing aliases, each mapped to a tag_id
    # normalized_key → tag_id
    candidates: dict[str, int] = {}

    for tag_id, name in conn.execute("SELECT id, name FROM cat.tags"):
        k = normalize(name)
        if k != TRASH:
            candidates[k] = tag_id

    for normalized, tag_id in conn.execute("SELECT normalized, tag_id FROM cat.tag_aliases"):
        candidates[normalized] = tag_id

    candidate_keys = list(candidates.keys())

    pending = conn.execute(
        "SELECT normalized FROM raw.unmapped_tags WHERE status = 'pending' ORDER BY freq DESC"
    ).fetchall()

    auto_rule = auto_fuzzy = suggested = untouched = 0

    for (key,) in pending:
        # L1 exact
        if key in candidates:
            _write_alias(conn, key, candidates[key], "rule", 1.0)
            _mark_resolved(conn, key)
            auto_rule += 1
            continue

        # L2 fuzzy
        if not candidate_keys:
            untouched += 1
            continue

        scores = [
            (fuzz.token_sort_ratio(key, ck), ck)
            for ck in candidate_keys
        ]
        best_score, best_key = max(scores, key=lambda x: x[0])
        best_tag_id = candidates[best_key]
        confidence = best_score / 100.0
        guarded = _collision_guard(key, best_key)

        if best_score >= _FUZZY_AUTO and not guarded:
            _write_alias(conn, key, best_tag_id, "fuzzy", confidence)
            _mark_resolved(conn, key)
            auto_fuzzy += 1
        elif best_score >= _FUZZY_REVIEW:
            _write_suggestion(conn, key, best_tag_id, "fuzzy", confidence)
            suggested += 1
        else:
            untouched += 1

    conn.commit()

    processed = len(pending)
    stats.update(
        processed=processed,
        auto_rule=auto_rule,
        auto_fuzzy=auto_fuzzy,
        suggested=suggested,
        untouched=untouched,
    )
    log.info(
        "processed=%d  rule=%d  fuzzy=%d  suggested=%d  untouched=%d",
        processed, auto_rule, auto_fuzzy, suggested, untouched,
    )


def _write_alias(conn, normalized: str, tag_id: int, source: str, confidence: float) -> None:
    conn.execute(
        """
        INSERT INTO cat.tag_aliases (normalized, tag_id, source, confidence)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (normalized) DO NOTHING
        """,
        (normalized, tag_id, source, confidence),
    )


def _mark_resolved(conn, normalized: str) -> None:
    conn.execute(
        "UPDATE raw.unmapped_tags SET status = 'resolved', updated_at = now()"
        " WHERE normalized = %s",
        (normalized,),
    )


def _write_suggestion(conn, normalized: str, tag_id: int, source: str, confidence: float) -> None:
    conn.execute(
        """
        UPDATE raw.unmapped_tags
        SET suggested_tag_id = %s, suggested_source = %s, suggested_confidence = %s,
            updated_at = now()
        WHERE normalized = %s
        """,
        (tag_id, source, confidence, normalized),
    )


if __name__ == "__main__":
    main()
