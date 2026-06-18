# specs/11-performers.md — Performers canonicalization + gender

Scope: turn `raw.raw_videos.performers_raw` (a flat `text[]` of surface names per
video) into first-class `cat.performers` + `cat.video_performers`, assign each
performer a **gender** (`female` / `male` / `trans` / `unknown`), and expose an
admin override. Unblocks actor pages `/actor/:slug` and performer-driven internal
linking (TODO Post-MVP). Mirrors the tags pipeline shape (spec 01/02): a
normalize() key, an aliases table, collect → canonicalize → apply, idempotent.

This spec covers canonicalization + gender ONLY. Actor *pages* (the performer-
analogue of `cat.page_candidates`, long-tail `:seo` slots) are a separate spec.

All decisions below are FINAL unless marked **OPEN**.

---

## 1. Why this is hard (data reality, measured 2026-06-18)

- 594,803 raw videos; 223,292 carry performers; **10,473 distinct surface names**.
- Catalog is **98% trans vertical** (581k trans / 13k mix) → vertical gives
  **no** per-performer gender signal.
- Tags are **video-level, not performer-level**. Scene tags exist
  (`shemale-fucks-girl`, `guy-fucks-shemale`, `shemale-and-female`) but
  `performers_raw` is an unordered name list — no role attribution, so we cannot
  deterministically map a name to the "shemale" vs "girl" role in a scene.
- Name prefixes are negligible: only ~63 of 10,473 names carry
  `Ts /ladyboy/tranny/shemale/...`.
- Feed `payload` jsonb has no gender (only `embed`, `preview_url`).
- Data is dirty: non-person strings leak in (`Anal Orgasm`, `No Hands Cum`,
  `Boy Toy`).
- The head is small and mostly trans: ≥100 videos = **501** names, ≥50 = 1008,
  ≥20 = 2117. Top names (Aubrey Kate, Natalie Mars, Casey Kisses, Chanel
  Santini…) are all well-known trans performers.

Conclusion: gender is **not deterministically derivable** from the catalog. It
needs an LLM batch pass (project rule 1 & 9 allow batch JSON with `source` +
`confidence`) plus an admin correction layer. Canonicalization itself IS
deterministic.

---

## 2. Decisions (fixed)

| #   | Decision | Value |
|-----|----------|-------|
| P1  | Schema shape | Three tables mirroring tags: `cat.performers`, `cat.performer_aliases`, `cat.video_performers`. Migration 015. |
| P2  | Scope floor (initial) | `MIN_VIDEOS = 100` → 501 performers promoted to `cat.performers`. Floor is a CLI arg (`--min-videos`), lowerable later without schema change. Names below the floor stay only as raw text (not canonicalized yet). |
| P3  | Gender domain | `female` \| `male` \| `trans` \| `unknown`. Stored with `gender_source` (`llm` \| `admin` \| `heuristic`) and `gender_confidence real`. |
| P4  | Gender method | **LLM batch classify** (head only) → admin override. NOT heuristic-only (can't split trans vs cis-female), NOT default-trans. |
| P5  | normalize(name) | Pure function, key-only (display name kept separate, mirrors tags D7). Lowercase → NFKD ASCII fold → strip leading gender/honorific prefixes (`ts`, `t-girl`, `tgirl`, `tranny`, `shemale`, `ladyboy`, `trans`) → strip punctuation → collapse whitespace. Result is the merge key, never shown. |
| P6  | Canonical display name | The **most frequent** surface form among a key's variants (ties → longest). `Izzy Wilde` (not `Ts Izzy Wilde`) when it dominates. |
| P7  | Merge policy | **Exact normalized-key match only** auto-merges. No fuzzy auto-merge in v1 — distinct people with similar names must not collapse silently. Fuzzy/duplicate cleanup is an admin action (Post-MVP). |
| P8  | Noise handling | LLM pass returns `is_person:false` → performer `status='hidden'` (not rendered, not deleted). Raw stays immutable (rule 2); hiding is a mapping on top. The `MIN_VIDEOS=100` floor already removes most one-off junk. |
| P9  | Idempotency | Every step upsert / `ON CONFLICT`. `apply` fully rebuilds `cat.video_performers` from the alias join; re-running converges. `n_videos` is a denormalized count refreshed by `apply`. |
| P10 | Prod workflow | **Build local → deploy to prod, one direction.** ALL performer data (canonicalize, apply, gender, admin overrides) is produced on the **local** DB. Prod receives it through the **existing data-sync** (pg_dump `--data-only` of `cat.performers` + `cat.performer_aliases` + `cat.video_performers`, restore on prod — the runbook pattern). No script writes performer data directly to prod; the only prod-side step is `db/migrate.py` (DDL). This keeps the invariant **prod = a deployed copy of local**, so a future migration or reset can never lose authored data that exists only on prod. (Semantic pages refresh-on-prod is the *documented exception*, justified by snapshot size; performer tables are small, so they follow the normal sync.) The 3 tables dump/restore together, so junction `video_id` / `performer_id` stay internally consistent. |

**OPEN (defer, not blocking):**
- O1: fuzzy/alias-merge admin tooling for near-duplicate names.
- O2: lowering `MIN_VIDEOS` below 100 once the head is curated.
- O3: per-performer "primary gender" when an alias key legitimately spans
  people — assume not needed at the ≥100 floor; revisit if it bites.

---

## 3. Schema (migration 015)

```sql
CREATE TABLE cat.performers (
    id                serial PRIMARY KEY,
    slug              text UNIQUE NOT NULL,          -- kebab(display name)
    name              text NOT NULL,                 -- canonical display (P6)
    gender            text NOT NULL DEFAULT 'unknown'
                        CHECK (gender IN ('female','male','trans','unknown')),
    gender_source     text,                          -- llm | admin | heuristic
    gender_confidence real,
    status            text NOT NULL DEFAULT 'active' -- active | hidden (P8)
                        CHECK (status IN ('active','hidden')),
    n_videos          int NOT NULL DEFAULT 0,        -- denormalized (P9)
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cat.performer_aliases (
    normalized   text PRIMARY KEY,                   -- normalize(surface) (P5)
    performer_id int NOT NULL REFERENCES cat.performers(id) ON DELETE CASCADE,
    source       text NOT NULL,                      -- 'collect' | 'admin'
    confidence   real,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cat.video_performers (
    video_id     bigint NOT NULL REFERENCES cat.videos(id) ON DELETE CASCADE,
    performer_id int    NOT NULL REFERENCES cat.performers(id) ON DELETE CASCADE,
    PRIMARY KEY (video_id, performer_id)
);
CREATE INDEX ON cat.video_performers (performer_id);
```

Mirrors `cat.tags` / `cat.tag_aliases` / `cat.video_tags`. No `ON CONFLICT`
target surprises: alias `normalized` PK, junction composite PK.

---

## 4. Pipeline (one script = one job; `python -m pipeline.performers.<x>`)

```
pipeline/performers/
    normalize.py        — normalize(name)->str ('' = skip) ; pure, unit-tested
    canonicalize.py     — scan raw, aggregate, promote freq>=MIN_VIDEOS to performers+aliases
    apply.py            — fill cat.video_performers via alias join; refresh n_videos
    classify_gender.py  — LLM batch: name+context -> {is_person,gender,confidence}
```

> No separate `collect` step: `normalize()` is Python (prefix-strip + NFKD), so
> the aggregate cannot be a pure-SQL `GROUP BY`. `canonicalize` does the single
> raw scan itself (normalize in Python, count keys + vote canonical surface
> form), keeping state in the DB and avoiding a redundant second scan.

### 4a. canonicalize
Single pass over `raw.raw_videos.performers_raw`: `normalize()` each name,
aggregate `(key → occurrence count, surface-form votes)`; `normalize()` → `''`
is skipped + counted. For each key with `freq >= MIN_VIDEOS` (default 100):
- pick canonical display name = most-frequent surface form (P6), `slug = kebab(name)`.
- upsert `cat.performers (slug, name)` `ON CONFLICT (slug)` → get id.
- upsert `cat.performer_aliases (normalized=key, performer_id, source='collect')`.
Batch via `executemany` (rule 10). Idempotent: re-run updates names, no dupes.

### 4b. apply
Rebuild `cat.video_performers` for promoted performers:
```sql
INSERT INTO cat.video_performers (video_id, performer_id)
SELECT v.id, a.performer_id
FROM cat.videos v
JOIN raw.raw_videos r                                -- promote link (spec 05):
  ON r.provider_id = v.provider_id                   --   no raw_video_id column;
 AND r.external_id = v.external_id                    --   matched on the UNIQUE pair
CROSS JOIN LATERAL unnest(r.performers_raw) AS s(name)
JOIN cat.performer_aliases a
  ON a.normalized = <normalize(s.name) in SQL or via staging>
ON CONFLICT DO NOTHING;
```
Then `UPDATE cat.performers SET n_videos = (count from junction)`.
Only `cat.videos` rows (promoted) get linked — site renders from `cat.*`.
Re-runnable; `ON CONFLICT DO NOTHING` + count refresh converge.

> Note: `normalize()` is Python (prefix list, NFKD). `apply` either (a) computes
> the alias key in Python while streaming, or (b) `collect` persists a
> `raw_video_id → normalized[]` staging map. Pick in implementation; (a) keeps
> state minimal, (b) keeps the join pure-SQL. Decide at build time, not here.

### 4c. classify_gender
LLM batch (rule 9). Input per performer: `name` + up to K sample video titles +
top co-occurring tags (cheap context). Prompt file:
`pipeline/llm/prompts/performer_gender.md`. Structured JSON out:
`{is_person: bool, gender: female|male|trans|unknown, confidence: 0..1}`.
Write `gender`, `gender_source='llm'`, `gender_confidence`; `is_person:false`
→ `status='hidden'`. Targets active performers with `gender_source IS NULL`
(re-runnable, only fills gaps). Runs anywhere (OpenRouter, no torch).

---

## 5. Admin (override layer)

New screen `admin/performers.py` (or page_screen): list performers (filter by
gender / status / `gender_source`), edit gender (sets `gender_source='admin'`,
`gender_confidence=1.0`), toggle `status` (hide noise the LLM missed). Admin
gender always wins over LLM. No alias-merge UI in v1 (O1).

---

## 6. Prod rollout (per P10 — build local, deploy to prod)

1. **Local:** `canonicalize` → `apply` → `classify_gender`. Review
   low-confidence + `hidden` rows in the local admin (admin reads the local DB).
2. **Migrate prod:** apply migration 015 on prod (`-L 15432` tunnel, prod creds
   from `/opt/lustdex/.env` — see prod-db-ops runbook). DDL only.
3. **Deploy data local→prod:** `pg_dump --data-only` of `cat.performers`,
   `cat.performer_aliases`, `cat.video_performers` from the local container →
   `TRUNCATE ... RESTART IDENTITY CASCADE` those 3 tables on prod →
   `pg_restore --data-only --disable-triggers --single-transaction`. Same
   mechanism as the catalog data-sync (runbook). Idempotent + repeatable: every
   later re-run of the local pipeline is re-deployed the same way. No LLM or
   pipeline script ever runs against prod.

---

## 7. Out of scope (separate specs / TODO)

- Actor pages `/actor/:slug` + long-tail `:seo` (performer × tag / × studio),
  the `cat.page_candidates` analogue. Depends on this spec.
- Studios canonicalization (`name-studio` axis) — independent track.
- Fuzzy de-duplication of performer names (O1).
