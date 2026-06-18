# TODO

## Phase: MVP

Goal: see the whole construction work end-to-end — DB → projection → site renders
homepage + tag pages + landing pages from `page_candidates`, on the laptop first,
then on prod VPS. Vectors, actor pages, tracking, admin polish, sibling-docker —
all out of MVP scope.

Stack lock-in:
- Site: **HonoX** on **Cloudflare Workers**, one Worker per domain
  (lustdts.com / lustdmilf.com / lustdexxx.com), shared codebase.
- DB connection: **Cloudflare Hyperdrive** → Postgres on VPS (prod).
- Dev mode: `wrangler dev` on the laptop, Hyperdrive binding routed to the
  local Docker Postgres from this repo's `docker-compose.yml`. Same code
  path, no deploy round-trip.
- VPS: ONLY Postgres + pipeline + backups. The site never touches VPS at runtime.

1. [x] **DB contract for site dev (HonoX).** `specs/07-site-db-contract.md`:
       allowed schema, per-table reference, projection WHERE helper, canonical
       queries (homepage, `/tag/:slug`, `/p/:slug`, `/go/:token`). Goal: site
       dev queries the DB without reading pipeline code.
2. [x] **HonoX site (local) — MVP surface.** `wrangler dev` against local
       Docker Postgres (this repo's `docker-compose.yml`), scoped by
       projection slug from env. Page types:
         - `/` — homepage (latest)
         - `/tag/:slug` — tag listing
         - `/p/:slug` — landing from `cat.page_candidates`
         - `/go/:token` — outbound redirect
         - service pages (about, dmca, 2257, terms, privacy) — static
       Run three instances locally (trans + milf + mix), one config per domain.
3. [x] **VPS: base setup** (lustdex-prod @ 158.69.112.39, Ubuntu 24.04,
       8 vCPU / 22 GB RAM / 193 GB disk, 4 GB swap, swappiness=10).
       SSH on port 21488 only, key-only, root disabled, AllowUsers sasha.
       UFW deny in / allow out. fail2ban (3 fails / 1h ban, systemd backend).
       sysstat + unattended-upgrades on.
4. [x] **VPS: production setup** — Postgres + pgvector in Docker on VPS
       (`pgvector/pgvector:pg17`, /opt/lustdex/, bind-mount pgdata),
       bound to 127.0.0.1:5432 (UFW unchanged), tuned for 22 GB RAM,
       all migrations applied via SSH tunnel from laptop. **Backups
       deferred** (destination undecided). Hyperdrive access path
       decided in MVP #5 (allowlist egress IPs OR CF Tunnel).
5. [x] **Hyperdrive setup** (CF Hyperdrive binding pointing at VPS Postgres,
       site env wiring) + deploy three Workers behind their domains.
6. [x] **Smoke check end-to-end** on prod (one projection live behind its
       domain; remaining two domains + DB dump/restore deferred — see Post-MVP).

## Post-MVP

- [x] **Semantic pages: which DB does the admin write to? (was BLOCKER).**
      Resolved via spec 10 §S19 (migration 014): store the encoded query on
      the row (`query_vec`), splitting the pipeline into **encode**
      (`query_text → query_vec`, laptop, model) and **snapshot**
      (`query_vec → video_ids`, pure SQL, runs anywhere). `query_text` is the
      durable source of truth; `video_ids` are derived and never carried
      local→prod. Workflow:
        1. Author rows on the laptop (CLI `create` or admin) — encodes
           `query_vec`, snapshots a local preview.
        2. Carry only the inputs to prod (`query_text`, `aliases`, `source`,
           `query_vec`) — NOT `video_ids`.
        3. `python -m pipeline.semantic_pages.refresh --all` on prod
           (pure SQL, no encoder) → `video_ids` re-snapshotted against prod
           embeddings, id-space correct.
      Backfill the 18 legacy prod rows once: `encode --all-missing` (laptop,
      DATABASE_URL→prod via `-L 15432`) then `refresh --all` on prod.
      NOTE: `cat.page_candidates` (Page Builder) still has the local-vs-prod
      ambiguity — its retrieval is tag-SQL, not vector, so it needs its own
      decision (likely just author-against-prod). Tracked separately below.
- [ ] **Page Builder (`cat.page_candidates`) local-vs-prod workflow.** Unlike
      semantic pages, retrieval is a tag join (no encoder), so the simplest
      fix is to author against prod (`DATABASE_URL` via tunnel) or add a
      member-id-only sync. Decide before curating more `/p/:slug` pages.
- [ ] **Deploy remaining two domains** (lustdmilf.com + one of
      lustdts.com/lustdexxx.com — whichever wasn't first). Same Worker
      codebase, per-domain projection env. Smoke check each.
- [ ] **Postgres backup: dump + restore drill.** Pick destination
      (off-VPS: B2/R2/external box), schedule `pg_dump` (Docker exec),
      retention policy, test restore into a sibling stack. Until this
      lands, VPS Postgres is single-copy.
- [x] **Embeddings / vectors** — MVP slice: trans projection only, 50k newest
      videos, `multilingual-e5-small` (384 halfvec, HNSW m=16/ef_construction=64).
      Spec: `specs/08-embeddings.md`. Migration: `011_video_embeddings.sql`.
      Runner: `python -m pipeline.embeddings.embed_videos --vertical trans
      --limit 50000`. Other verticals + larger samples = same script, separate
      runs (table covers all).
- [ ] **Vector search** endpoint on the site (depends on embeddings).
- [x] **Performers canonicalization (was BLOCKER).** `specs/11-performers.md`
      + migration 015. `cat.performers` / `cat.performer_aliases` /
      `cat.video_performers` (mirrors tags). Pipeline:
      `canonicalize` (scan raw, promote freq≥100 → 501 performers,
      exact-normalized merge: `Ts Izzy Wilde`=`Izzy Wilde`) →
      `apply` (fill junction, refresh `n_videos`) →
      `classify_gender` (LLM batch: `{is_person,gender,confidence}`,
      `gender_source='llm'`) + admin override (`gender_source='admin'`).
      Deterministic from `raw.*` → runs directly on prod; never carry junction
      ids local→prod (id-space, spec 11 §P10). Verified local: 501 performers,
      150784 links, gender pass classifies top trans performers correctly.
      NOTE: floor=100 initial (`--min-videos` lowerable). Run on prod +
      full gender pass still pending (below).
- [ ] **Performers: prod rollout + full gender pass.** Apply migration 015 on
      prod, run `canonicalize`/`apply` against prod (reads prod `raw.*`), then
      `classify_gender` over all 501 (`deepseek/deepseek-v4-flash`), review
      low-confidence + `hidden` rows in admin. Lower `--min-videos` later for
      more coverage.
- [ ] **Studios canonicalization** — no studio field exists today (not in
      `cat.tags`, not first-class in `raw.raw_videos`). Needed if we want
      `name-studio` SEO pages. Spec first: where does studio come from
      (feed payload? per-provider extractor?), `cat.studios` schema,
      `cat.video_studios`. Independent of performers.
- [ ] **Actor pages** `/actor/:slug` (canonical performer page) and
      `/actor/:slug/:seo` (long-tail SEO variants, e.g. `lance-hart/blowjob`,
      `lance-hart/studio-xyz`). The `:seo` slot is fed by a performer-
      analogue of `cat.page_candidates`: tuples `(performer_id, tag_id)` or
      `(performer_id, studio_id)` with co-occurrence threshold + admin
      approval (analogous to current Page Builder). Depends on performers
      canonicalization; `name-studio` axis additionally depends on studios
      canonicalization.
- [ ] **Impression/click tracking** via CF Analytics Engine. Setup datasets,
      writeDataPoint in Worker, dashboards.
- [ ] **Video detail page** (thumbnail/preview via `go_token`, Bunny CDN).
      Already in demo; port to prod site after MVP ships.
- [ ] **Related videos block** on detail page (uses embeddings).
- [ ] **Raised `MIN_VIDEOS_PER_PAGE` threshold** (spec 06 §5; MVP uses 1).
- [ ] **Admin: projections editor** (currently SQL-only; spec 06 P9).
- [ ] **Admin: page_candidates CRUD as long-tail override.** Auto-generator
      (Page Builder) + `brand_tag_id` filter (migration 010) covers the bulk
      cleanly. Manual editor (title, slug, tag-tuple, projection scope) is
      needed for long-tail intent splits Google distinguishes but the auto
      can't — e.g. `ladyboy` / `tgirl` / `ts-girl` vs the canonical `shemale`.
- [ ] **Admin: Pipeline tab** with trigger buttons (each step = its own button,
      not chained).
        - Buttons: Ingest [provider ▾] [limit N] / Promote raw→cat / Tags
          cascade / Upload thumbs / Upload previews
        - Mechanism: subprocess.Popen in background, tail log under button
        - State already in raw.pipeline_runs (Runs screen shows history)
        - Implementation note: `admin/tags/runs.py` already does this for
          tags Collect/Cascade/Apply — extend the `_JOBS` list (or split per
          tab) to cover ingest/promote/upload_thumbs/upload_previews.
- [ ] **Admin: Pipeline funnel dashboard** — counts per stage so you see at a
      glance where the catalogue is leaking. Not "runs history" (already in
      `raw.pipeline_runs`); this is per-row state of the catalogue itself.
        - Rows: ingested (raw.raw_videos) → promoted (cat.videos) → tagged
          (≥1 row in cat.video_tags) → thumbs uploaded (has_thumb=true) →
          in page_candidates. Each row: total, pending (delta from previous
          stage), % done. Per-projection breakdown using the §4 WHERE snippet.
        - Motivation: site listings filter on `has_thumb=true` (spec 07 §5.x),
          so fresh ingests are invisible until `pipeline.media.upload_thumbs`
          runs — easy to forget. Dashboard makes the gap loud.
- [ ] **Admin: reserve `/` (default landing) for a general Dashboard section**;
      move Tags canonicalization (Overview/Review/Dictionary/Runs) off the
      default. Dashboard content TBD — leave placeholder.
- [ ] **Refactor: provider/site split.** Today `cat.providers` doubles as the
      site dimension — every zilla.cash sister site (around.xxx, analdin.com,
      xozilla.com, xtits.com, …) is its own cat.providers row, because
      `cat.videos.UNIQUE(provider_id, external_id)` doesn't tolerate shared
      providers across independent external_id namespaces. Proper shape:
      `ALTER TABLE raw.sites SET SCHEMA cat`, add `cat.videos.site_id` (NOT
      NULL, FK), swap unique to `(site_id, external_id)`, collapse zilla.cash
      sites back under one provider row, update promote + ingest seeds + spec
      07 §3. Out of scope until catalog growth (or another schema change in
      this area) makes the mismatch concretely painful.
- [ ] **Manual: sibling Docker stack** that mirrors prod VPS Postgres
      (same version, extensions, RAM/conf) — for rehearsing migrations safely.
- [ ] **Resolve spec 07 drift / source-of-truth.** Sibling repo
      `lustdts_com/specs/07-site-db-contract.md` has site-specific edits
      Lustdex doesn't: §5.4 `/watch/{slug}-{token}` routing with slug-verify
      301, CDN host `cdn.lustdexxx.com`, C1 framing as one-repo-per-Worker.
      Lustdex copy has brand-tag refinements lustdts_com mostly has but
      misses §5.3 inline (patched in lustdts_com 2026-06-14). Pick canonical
      location + sync convention (symlink? one-way merge?). Also reconciles
      a real product decision: TODO MVP #2 says "shared codebase, three
      Workers" but lustdts_com is its own repo — which wins.
- [ ] **Ingest: ashemaletube.com feed.** Admin endpoint base:
      `https://adminex.ashemaletube.com/rss-final/?SubmitCheck=<JWT>&h=<hash>
      &val1..val7=<knobs>&routerDomain=router&number=N&size=WxH&niche=0
      &quality=0&submit=Send`. SubmitCheck is a ~1h TTL signed JWT minted
      by the admin panel (sample URL pasted in chat 2026-06-14). Open
      questions to resolve before writing the parser: how to obtain a fresh
      token unattended (login flow? long-lived alt endpoint?), feed format
      (RSS XML vs CSV — name says rss-final), column/element schema,
      provider/site rows in `raw.providers`/`raw.sites`, niche=0 mapping.

## Done
- [x] Scaffolding per FOUNDATION.md (structure, docker, migration 001, common/)
- [x] specs/01-tags.md — normalize() rules + cascade thresholds
- [x] Ingest: first provider feed parser (zilla_cash.py + adultnext.py)
- [x] specs/05-schema-split.md + migration 007 + `pipeline/promote/videos.py`
- [x] specs/06-projections.md + migration 008 (`cat.videos.vertical` +
      `cat.projections`) + promote update for vertical
- [x] Migration 009: projection domains (lustdts/lustdmilf/lustdexxx) +
      mix-hetero projection (`from_verticals=['mix']`, exclude shemale+gay)
