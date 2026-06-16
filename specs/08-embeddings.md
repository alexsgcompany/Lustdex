# specs/08-embeddings.md — Video embeddings (MVP, trans-projection only)

Scope: produce one semantic vector per video for the `trans` projection, store
in Postgres with an HNSW index, expose nothing on the site yet. This is the
data-side prerequisite for future related-videos / semantic-search features.

All decisions below are FINAL for the MVP slice. Revisit when scaling to other
projections or swapping the model.

---

## 1. Decisions (fixed)

| #  | Decision | Value |
|----|----------|-------|
| E1 | Model | `intfloat/multilingual-e5-small` (384 dim, multilingual, 512-token context) |
| E2 | Storage type | `halfvec(384)` — 16-bit floats, half the disk vs `vector(384)`, near-zero recall loss for e5-class models |
| E3 | Index | HNSW, `halfvec_cosine_ops`, `m=16`, `ef_construction=200`. Initial migration 011 used the pgvector default 64; A/B compared on 50k (sibling table, same vectors, different ef_construction) showed marginal recall change at that scale but the higher value was locked in before the full 581k embed to amortise the one-time build cost. Migration 012 re-indexes. |
| E4 | Sample for MVP | `cat.videos.vertical = 'trans'`, newest first (`published_at DESC NULLS LAST, id DESC`), `LIMIT 50000` |
| E5 | Input text composition | `"passage: {title}. tags: {tags}. performers: {performers}. {description}"`. `passage:` prefix is required by the E5 family for indexed content. Tags joined `", "` from `cat.tags.name` (canonical only). Performers joined `", "` from `raw.raw_videos.performers_raw` (raw text, no canonicalization yet). Empty parts collapse cleanly (no stray separators). |
| E6 | Truncation | model-side, `max_seq_length=512`, `truncation=True`. No pre-trim in Python. |
| E7 | Idempotency | per-row `input_hash = sha256(input_text)` stored alongside the vector. Re-runs SELECT current hashes for candidate `video_id`s and skip rows whose hash is unchanged. No re-encoding on no-op. |
| E8 | Device | auto: CUDA → MPS (Mac) → CPU. e5-small is small enough that CPU works (slow); MPS is fine for 50k. |
| E9 | New deps | `sentence-transformers`, `torch` (approved). No `pgvector` Python adapter — embeddings are passed as text literals (`'[v1,v2,...]'`) and cast via column type; avoids an extra dep for one INSERT. |
| E10 | Schema location | `cat.video_embeddings`. PK = `video_id` (one embedding per video). FK to `cat.videos(id) ON DELETE CASCADE`. Stored in `cat.*` because the site (Hyperdrive → cat schema only) will read it for future related-videos. |
| E11 | Performers handling | use `raw.raw_videos.performers_raw` as-is. Performer canonicalization is a separate Post-MVP item; when it lands, re-embed (input text changes, hash mismatches, scripts pick it up automatically). |
| E12 | Description source | `raw.raw_videos.description`. Not promoted to `cat.videos` — embedding script reads cross-schema; this is fine because the embedding script is pipeline-side, not site-side. |
| E13 | One model = one column | not generalising to "multiple models per video" in MVP. If we add another model later (e.g. e5-large for higher recall), add a second table `cat.video_embeddings_<model>` rather than widening this one. |

---

## 2. Schema (migration 011)

```sql
CREATE TABLE cat.video_embeddings (
    video_id   bigint PRIMARY KEY REFERENCES cat.videos(id) ON DELETE CASCADE,
    model      text NOT NULL,                 -- 'intfloat/multilingual-e5-small'
    embedding  halfvec(384) NOT NULL,
    input_hash bytea NOT NULL,                -- sha256 of input text
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Final HNSW parameters (migration 012 supersedes the ef_construction=64 used in 011).
CREATE INDEX video_embeddings_hnsw
    ON cat.video_embeddings
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);
```

Notes:
- pgvector ≥ 0.7 ships `halfvec` and `halfvec_cosine_ops`. The bundled
  `pgvector/pgvector:pg17` image satisfies this.
- `input_hash` is `bytea` (raw 32-byte sha256), not text — half the storage
  and avoids encoding ambiguity.
- No separate `input_text` column: the text is reproducible from
  `cat.videos.title + raw.raw_videos.description + cat.video_tags + raw.raw_videos.performers_raw`
  at any time. Storing it would duplicate raw data.

---

## 3. Selection query (canonical)

```sql
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
ORDER BY v.published_at DESC NULLS LAST, v.id DESC
LIMIT %(limit)s;
```

`vertical='trans'`, `limit=50000` for the MVP slice. Same query works for any
other vertical when we expand.

---

## 4. Idempotency contract

Per E7:

1. Build `(video_id, input_text, input_hash)` triples in memory for the
   candidate set.
2. `SELECT video_id, input_hash FROM cat.video_embeddings
    WHERE video_id = ANY(%s)` — map existing hashes.
3. For each candidate, skip if `existing[video_id] == input_hash`.
4. Encode the remaining candidates (batched, GPU/MPS/CPU per E8).
5. Upsert `(video_id, model, embedding, input_hash)` with
   `ON CONFLICT (video_id) DO UPDATE SET embedding=EXCLUDED.embedding,
    input_hash=EXCLUDED.input_hash, model=EXCLUDED.model, created_at=now()`.

Re-running with no source changes is a no-op. Title/description/tag/performer
changes trigger automatic re-embedding on the next run.

---

## 5. CLI

```
python -m pipeline.embeddings.embed_videos \
    --vertical trans \
    --limit 50000 \
    --batch 64
```

Flags:
- `--vertical` (default: `trans`) — `cat.videos.vertical` filter.
- `--limit` (default: `50000`) — cap candidate set.
- `--batch` (default: `64`) — encode batch size.
- `--model` (default: `intfloat/multilingual-e5-small`) — pinned to E1; flag
  exists for future swaps.
- `--device` (default: `auto`) — `cuda` / `mps` / `cpu` / `auto`.

Progress to stdout (per common/log.py): one INFO per batch with throughput
(`embedded N/total`), final summary line (`embedded X, skipped Y`).

---

## 6. Out of scope (Post-MVP)

- Embeddings for other verticals (`milf`, `mix`) — same script, different
  `--vertical` flag, separate runs. Index already covers all rows.
- Site-side related-videos / search endpoint — separate spec.
- Embedding query side (queries need `"query: "` prefix, not `"passage: "`).
  Implement when the search endpoint lands.
- Performer-aware re-embedding strategy after performer canonicalization
  (handled "for free" by the hash check, but may want batched re-encode).
- Re-tuning HNSW (`m`, `ef_construction`, `ef_search`) — defer until measured
  recall/latency data exists.
- Model upgrade path (e5-large, BGE-m3) — E13 covers the schema shape.
