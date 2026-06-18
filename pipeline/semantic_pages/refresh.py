"""Re-snapshot semantic landing pages against current embeddings.

See specs/10-semantic-pages.md §4 (snapshot algorithm) and §6.2 (CLI).

Pure SQL: the search uses the row's stored `query_vec` (spec 10 §S19), so this
runs anywhere — including prod — with no encoder loaded. Rows whose `query_vec`
is NULL (never encoded, or stale after a model swap) are skipped; encode them
first with `python -m pipeline.semantic_pages.encode`.

Usage:
    python -m pipeline.semantic_pages.refresh <id>
    python -m pipeline.semantic_pages.refresh --stale-only
    python -m pipeline.semantic_pages.refresh --all
"""

import argparse

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger("semantic_pages.refresh")

# Spec 10 defaults.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"   # S5
TOP_K = 1000                                # S2
MAX_DIST = 0.40                             # S3
EF_SEARCH = 400                             # spec 09 §V7
STALE_DAYS = 30                             # S7
# Query-side prefix (spec 09 §V3 / S5). Indexed side uses no prefix (spec 08).
# Kept here so the encoder (pipeline.semantic_pages.encode) shares one source.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Snapshot search using the row's stored query_vec — no encoder needed.
# Captures the projection-agnostic universe; has_thumb filtered out (§4: a
# thumbless video never renders, so capturing it wastes a slot).
SEARCH_SQL = """
WITH q AS (SELECT query_vec FROM cat.semantic_pages WHERE id = %(id)s)
SELECT e.video_id, e.embedding <=> q.query_vec AS dist
FROM cat.video_embeddings e
JOIN cat.videos v ON v.id = e.video_id AND v.has_thumb = true
CROSS JOIN q
ORDER BY e.embedding <=> q.query_vec
LIMIT %(top_k)s
"""

SELECT_ALL = "SELECT id FROM cat.semantic_pages ORDER BY id"
SELECT_STALE = """
SELECT id FROM cat.semantic_pages
WHERE embedding_model != %(model)s
   OR refreshed_at < now() - make_interval(days => %(days)s)
ORDER BY id
"""


def snapshot_one(conn, row_id, top_k, max_dist, ef_search):
    """Re-snapshot a single row in place from its stored query_vec.

    Pure SQL: does NOT touch embedding_model — that reflects which encoder
    produced query_vec and only changes on re-encode (pipeline...encode).
    Returns (captured, kept); (0, 0) if the row is missing or has no query_vec.
    """
    has_vec = conn.execute(
        "SELECT query_vec IS NOT NULL FROM cat.semantic_pages WHERE id = %s",
        (row_id,),
    ).fetchone()
    if has_vec is None:
        log.warning("row id=%s not found, skipping", row_id)
        return (0, 0)
    if not has_vec[0]:
        log.warning("row id=%s has no query_vec, run `encode` first, skipping", row_id)
        return (0, 0)

    with conn.cursor() as cur:
        # SET LOCAL rejects bind params; ef_search is a validated int.
        cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
        cur.execute(SEARCH_SQL, {"id": row_id, "top_k": top_k})
        hits = cur.fetchall()

    captured = len(hits)
    # Ordered by dist ASC from SQL; keep only those under the cutoff (S3).
    kept_ids = [vid for vid, dist in hits if dist < max_dist]

    conn.execute(
        """
        UPDATE cat.semantic_pages
        SET video_ids = %s, top_k = %s, max_dist = %s, refreshed_at = now()
        WHERE id = %s
        """,
        (kept_ids, top_k, max_dist, row_id),
    )
    conn.commit()
    log.info("refreshed id=%s captured=%d kept=%d", row_id, captured, len(kept_ids))
    return (captured, len(kept_ids))


def select_targets(conn, args) -> list[int]:
    if args.id is not None:
        return [args.id]
    if args.stale_only:
        rows = conn.execute(
            SELECT_STALE, {"model": args.model, "days": STALE_DAYS}
        ).fetchall()
        return [r[0] for r in rows]
    rows = conn.execute(SELECT_ALL).fetchall()
    return [r[0] for r in rows]


def run(args) -> int:
    with get_conn() as conn:
        targets = select_targets(conn, args)
        log.info("targets: %d rows", len(targets))
        if not targets:
            return 0
        if args.dry_run:
            log.info("dry-run: would refresh ids=%s", targets)
            return 0

        log.info("top_k=%d max_dist=%.2f ef_search=%d",
                 args.top_k, args.max_dist, args.ef_search)
        total_kept = 0
        for row_id in targets:
            _, kept = snapshot_one(
                conn, row_id, args.top_k, args.max_dist, args.ef_search,
            )
            total_kept += kept
        log.info("done. rows=%d total_kept=%d", len(targets), total_kept)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("id", nargs="?", type=int, help="single row id")
    ap.add_argument("--stale-only", action="store_true",
                    help="rows with mismatched model or older than %d days" % STALE_DAYS)
    ap.add_argument("--all", action="store_true", help="every row")
    ap.add_argument("--top-k", type=int, default=TOP_K, dest="top_k")
    ap.add_argument("--max-dist", type=float, default=MAX_DIST, dest="max_dist")
    ap.add_argument("--ef-search", type=int, default=EF_SEARCH, dest="ef_search")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="current default, for --stale-only comparison only")
    ap.add_argument("--dry-run", action="store_true")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.id is None and not args.stale_only and not args.all:
        log.error("specify a row id, --stale-only, or --all")
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
