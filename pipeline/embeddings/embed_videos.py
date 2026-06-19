"""Embed videos in a given projection vertical with multilingual-e5-small.

See specs/08-embeddings.md.

Usage:
    python -m pipeline.embeddings.embed_videos --vertical trans --limit 50000
"""

import argparse
import hashlib

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger("embeddings.embed_videos")

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
SELECT_CANDIDATES_SQL = """
SELECT
    v.id,
    v.title,
    rv.description,
    rv.performers_raw,
    COALESCE(
        (SELECT array_agg(t.name ORDER BY t.name)
         FROM cat.video_tags vt
         JOIN cat.tags t ON t.id = vt.tag_id
         WHERE vt.video_id = v.id),
        '{}'
    ) AS tags
FROM cat.videos v
JOIN raw.raw_videos rv ON rv.id = v.id
WHERE v.vertical = %(vertical)s
/*only_missing*/
ORDER BY v.published_at DESC NULLS LAST, v.id DESC
LIMIT %(limit)s
"""

# Opt-in filter for bulk backfill: skip rows that already have ANY embedding,
# so successive --limit chunks advance instead of re-scanning the newest slice.
# Trades off content-update re-embedding (the in-Python hash recheck) for speed.
ONLY_MISSING_CLAUSE = (
    "AND NOT EXISTS (SELECT 1 FROM cat.video_embeddings e WHERE e.video_id = v.id)"
)

UPSERT_SQL = """
INSERT INTO cat.video_embeddings (video_id, model, embedding, input_hash)
VALUES (%s, %s, %s::halfvec, %s)
ON CONFLICT (video_id) DO UPDATE
SET embedding  = EXCLUDED.embedding,
    input_hash = EXCLUDED.input_hash,
    model      = EXCLUDED.model,
    created_at = now()
"""


def build_passage(
    title: str | None,
    description: str | None,
    tags: list[str] | None,
    performers: list[str] | None,
) -> str:
    """Compose the passage text per spec 08 §E5.

    No model-specific prefix: bge-small-en-v1.5 does not require one on the
    indexed side (the 'Represent this sentence...' instruction is query-side
    only, applied in the Worker — see spec 09 §V3).
    """
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    if tags:
        clean = [t.strip() for t in tags if t and t.strip()]
        if clean:
            parts.append("tags: " + ", ".join(clean))
    if performers:
        clean = [p.strip() for p in performers if p and p.strip()]
        if clean:
            parts.append("performers: " + ", ".join(clean))
    if description:
        parts.append(description.strip())
    return ". ".join(parts)


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def vec_literal(vec) -> str:
    """Format a 384-dim numpy/list vector as a pgvector text literal."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def run(vertical: str, limit: int, batch: int, model_name: str, device: str,
        only_missing: bool = False) -> int:
    device = pick_device(device)
    log.info("device=%s model=%s vertical=%s limit=%d only_missing=%s",
             device, model_name, vertical, limit, only_missing)

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device=device)

    sql = SELECT_CANDIDATES_SQL.replace(
        "/*only_missing*/", ONLY_MISSING_CLAUSE if only_missing else ""
    )
    with get_conn() as conn:
        rows = conn.execute(
            sql, {"vertical": vertical, "limit": limit}
        ).fetchall()
        log.info("candidates: %d", len(rows))
        if not rows:
            return 0

        candidates = []
        for vid, title, description, performers, tags in rows:
            text = build_passage(title, description, tags, performers)
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            candidates.append((vid, text, digest))

        video_ids = [c[0] for c in candidates]
        existing = dict(
            conn.execute(
                "SELECT video_id, input_hash FROM cat.video_embeddings WHERE video_id = ANY(%s)",
                (video_ids,),
            ).fetchall()
        )

        pending = [c for c in candidates if bytes(existing.get(c[0], b"")) != c[2]]
        skipped = len(candidates) - len(pending)
        log.info("pending: %d  skipped (hash unchanged): %d", len(pending), skipped)
        if not pending:
            return 0

        embedded = 0
        for chunk in chunks(pending, 1000):
            texts = [c[1] for c in chunk]
            vectors = model.encode(
                texts,
                batch_size=batch,
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            assert vectors.shape == (len(chunk), DIM), f"unexpected shape {vectors.shape}"
            params = [
                (c[0], model_name, vec_literal(v), c[2])
                for c, v in zip(chunk, vectors, strict=True)
            ]
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, params)
            conn.commit()
            embedded += len(chunk)
            log.info("embedded %d / %d", embedded, len(pending))

        log.info("done. embedded=%d skipped=%d", embedded, skipped)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", default="trans")
    ap.add_argument("--limit", type=int, default=50000)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--only-missing", action="store_true",
                    help="skip rows that already have an embedding (bulk backfill)")
    args = ap.parse_args()
    return run(args.vertical, args.limit, args.batch, args.model, args.device,
               args.only_missing)


if __name__ == "__main__":
    raise SystemExit(main())
