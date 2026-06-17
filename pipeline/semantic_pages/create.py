"""Create a semantic landing page from operator input, then snapshot it.

See specs/10-semantic-pages.md §6.1.

Usage:
    python -m pipeline.semantic_pages.create \\
        --query "shemale big dick orgy" \\
        --source operator \\
        --source-volume 880 \\
        --alias "ts big dick orgy" --alias "tranny big cock orgy"
"""

import argparse
import re
import unicodedata

import psycopg

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.semantic_pages.refresh import (
    DEFAULT_MODEL,
    EF_SEARCH,
    MAX_DIST,
    TOP_K,
    pick_device,
    snapshot_one,
)

log = get_logger("semantic_pages.create")

SLUG_MAX = 60
SOURCES = ("operator", "gsc", "site_search", "serpstat")


def kebab(text: str) -> str:
    """Slugify query_text (spec 10 §S12). Mirrors promote.videos._slug."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        return "s"
    if len(s) > SLUG_MAX:
        head = s[:SLUG_MAX]
        cut = head.rsplit("-", 1)[0]
        s = cut or head
    return s


INSERT_SQL = """
INSERT INTO cat.semantic_pages
    (query_text, aliases, source, source_volume, slug_provisional,
     video_ids, top_k, max_dist, embedding_model, status)
VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s, 'draft')
RETURNING id
"""


def run(args) -> int:
    slug = kebab(args.query)
    with get_conn() as conn:
        try:
            row = conn.execute(
                INSERT_SQL,
                (args.query, args.alias, args.source, args.source_volume,
                 slug, args.top_k, args.max_dist, args.model),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            log.error("query_text already exists: %r (merge aliases manually)", args.query)
            return 1
        row_id = row[0]
        conn.commit()
        log.info("created id=%d slug=%s status=draft", row_id, slug)

        # §6.1 step 4: snapshot the new row immediately.
        device = pick_device(args.device)
        log.info("snapshot device=%s model=%s", device, args.model)
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(args.model, device=device)
        snapshot_one(
            conn, model, row_id, args.model,
            args.top_k, args.max_dist, args.ef_search,
        )

    print(f"id={row_id} slug={slug}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--source", required=True, choices=SOURCES)
    ap.add_argument("--source-volume", type=int, default=None, dest="source_volume")
    ap.add_argument("--alias", action="append", default=[], dest="alias")
    ap.add_argument("--top-k", type=int, default=TOP_K, dest="top_k")
    ap.add_argument("--max-dist", type=float, default=MAX_DIST, dest="max_dist")
    ap.add_argument("--ef-search", type=int, default=EF_SEARCH, dest="ef_search")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
