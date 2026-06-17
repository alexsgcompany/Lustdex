"""Re-snapshot semantic landing pages against current embeddings.

See specs/10-semantic-pages.md §4 (snapshot algorithm) and §6.2 (CLI).

Usage:
    python -m pipeline.semantic_pages.refresh <id>
    python -m pipeline.semantic_pages.refresh --stale-only
    python -m pipeline.semantic_pages.refresh --all
"""

import argparse

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.embeddings.embed_videos import pick_device, vec_literal

log = get_logger("semantic_pages.refresh")

# Spec 10 defaults.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"   # S5
TOP_K = 1000                                # S2
MAX_DIST = 0.40                             # S3
EF_SEARCH = 400                             # spec 09 §V7
STALE_DAYS = 30                             # S7
# Query-side prefix (spec 09 §V3 / S5). Indexed side uses no prefix (spec 08).
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Capture the projection-agnostic universe of videos this query is about.
# has_thumb filter is in the snapshot (S/§4): a thumbless video never renders.
SEARCH_SQL = """
SELECT e.video_id, e.embedding <=> %(vec)s::halfvec AS dist
FROM cat.video_embeddings e
JOIN cat.videos v ON v.id = e.video_id AND v.has_thumb = true
ORDER BY e.embedding <=> %(vec)s::halfvec
LIMIT %(top_k)s
"""

SELECT_ALL = "SELECT id FROM cat.semantic_pages ORDER BY id"
SELECT_STALE = """
SELECT id FROM cat.semantic_pages
WHERE embedding_model != %(model)s
   OR refreshed_at < now() - make_interval(days => %(days)s)
ORDER BY id
"""


def snapshot_one(conn, model, row_id, model_name, top_k, max_dist, ef_search):
    """Re-snapshot a single row in place. Returns (captured, kept)."""
    query_text = conn.execute(
        "SELECT query_text FROM cat.semantic_pages WHERE id = %s", (row_id,)
    ).fetchone()
    if query_text is None:
        log.warning("row id=%s not found, skipping", row_id)
        return (0, 0)
    query_text = query_text[0]

    vec = model.encode(
        QUERY_PREFIX + query_text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )

    with conn.cursor() as cur:
        # SET LOCAL rejects bind params; ef_search is a validated int.
        cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
        cur.execute(
            SEARCH_SQL,
            {"vec": vec_literal(vec), "top_k": top_k},
        )
        hits = cur.fetchall()

    captured = len(hits)
    # Ordered by dist ASC from SQL; keep only those under the cutoff (S3).
    kept_ids = [vid for vid, dist in hits if dist < max_dist]

    conn.execute(
        """
        UPDATE cat.semantic_pages
        SET video_ids = %s, top_k = %s, max_dist = %s,
            embedding_model = %s, refreshed_at = now()
        WHERE id = %s
        """,
        (kept_ids, top_k, max_dist, model_name, row_id),
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

        device = pick_device(args.device)
        log.info("device=%s model=%s top_k=%d max_dist=%.2f ef_search=%d",
                 device, args.model, args.top_k, args.max_dist, args.ef_search)
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(args.model, device=device)

        total_kept = 0
        for row_id in targets:
            _, kept = snapshot_one(
                conn, model, row_id, args.model,
                args.top_k, args.max_dist, args.ef_search,
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
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
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
