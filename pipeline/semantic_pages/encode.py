"""Encode query_text -> query_vec for semantic landing pages (laptop-side).

See specs/10-semantic-pages.md §S19 / §6.3.

This is the ONE step that needs the encoder. It turns a row's `query_text` into
the stored `query_vec` and stamps `embedding_model`. Once stored, snapshotting
(`pipeline.semantic_pages.refresh`) is pure SQL and runs anywhere — including
prod — with no model. Re-run this after a model swap (S18): a row whose
`embedding_model` no longer matches the default carries a stale `query_vec`.

Usage:
    python -m pipeline.semantic_pages.encode --id 42
    python -m pipeline.semantic_pages.encode --all-missing   # query_vec IS NULL
    python -m pipeline.semantic_pages.encode --stale         # model != default
"""

import argparse

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.embeddings.embed_videos import pick_device, vec_literal
from pipeline.semantic_pages.refresh import DEFAULT_MODEL, QUERY_PREFIX

log = get_logger("semantic_pages.encode")

SELECT_MISSING = "SELECT id FROM cat.semantic_pages WHERE query_vec IS NULL ORDER BY id"
SELECT_STALE = (
    "SELECT id FROM cat.semantic_pages WHERE embedding_model != %(model)s ORDER BY id"
)


def load_model(model_name: str, device: str):
    from sentence_transformers import SentenceTransformer

    dev = pick_device(device)
    log.info("loading encoder model=%s device=%s", model_name, dev)
    return SentenceTransformer(model_name, device=dev)


def encode_query(model, query_text: str) -> str:
    """Encode one query into a halfvec literal (query-side prefix, normalized)."""
    vec = model.encode(
        QUERY_PREFIX + query_text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vec_literal(vec)


def store_vec(conn, row_id: int, model, model_name: str) -> bool:
    """Encode the row's query_text and store query_vec + embedding_model."""
    row = conn.execute(
        "SELECT query_text FROM cat.semantic_pages WHERE id = %s", (row_id,)
    ).fetchone()
    if row is None:
        log.warning("row id=%s not found, skipping", row_id)
        return False
    vec = encode_query(model, row[0])
    conn.execute(
        "UPDATE cat.semantic_pages SET query_vec = %s::halfvec, embedding_model = %s "
        "WHERE id = %s",
        (vec, model_name, row_id),
    )
    conn.commit()
    log.info("encoded id=%s model=%s", row_id, model_name)
    return True


def select_targets(conn, args) -> list[int]:
    if args.id is not None:
        return [args.id]
    if args.all_missing:
        rows = conn.execute(SELECT_MISSING).fetchall()
        return [r[0] for r in rows]
    rows = conn.execute(SELECT_STALE, {"model": args.model}).fetchall()
    return [r[0] for r in rows]


def run(args) -> int:
    with get_conn() as conn:
        targets = select_targets(conn, args)
        log.info("targets: %d rows", len(targets))
        if not targets:
            return 0
        model = load_model(args.model, args.device)
        done = sum(store_vec(conn, rid, model, args.model) for rid in targets)
        log.info("done. encoded %d rows", done)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, help="single row id")
    ap.add_argument("--all-missing", action="store_true", dest="all_missing",
                    help="rows with query_vec IS NULL")
    ap.add_argument("--stale", action="store_true",
                    help="rows whose embedding_model != --model (post model swap)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    args = ap.parse_args()
    if args.id is None and not args.all_missing and not args.stale:
        log.error("specify --id, --all-missing, or --stale")
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
