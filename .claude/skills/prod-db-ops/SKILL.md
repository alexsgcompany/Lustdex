---
name: prod-db-ops
description: Runbook + guardrails for any operation against the PRODUCTION Postgres (lustdex-db on the VPS) — applying migrations, deploying data, refreshing derived tables, or querying prod. Use whenever a command targets prod (ssh sasha@158.69.112.39, port 21488, container lustdex-db, or DATABASE_URL on 127.0.0.1:5433). Encodes the prod≠local reality, the two access methods, and the authored-vs-derived deploy decision. Update this file when the prod setup or a deploy nuance changes.
---

# prod-db-ops

Operate on the **production** database safely. Prod is small, live, and **not** a
copy of local — most footguns come from assuming it is. Read the decision tree
before mutating anything.

## 0. Facts about prod (verify if stale — this file can drift)

| thing | value |
|---|---|
| host / ssh | `sasha@158.69.112.39 -p 21488` (key auth; **root disabled**, AllowUsers sasha) |
| pg container | **`lustdex-db`** — NOT `catalog-db` (that is the LOCAL container) |
| db / user | `catalog` / `catalog` |
| pg exposed | `127.0.0.1:5432` on the prod host (loopback only) |
| live sites | **trans only** (lustdts.com). milf + mix (lustdexxx.com) NOT launched yet |
| prod catalogue | SMALL & different from local: ~trans 581k, mix ~13k. Local has the full mix 892k dump that was **never** deployed |
| HNSW | `video_embeddings_hnsw` already present on prod (m=16, ec=200) |
| migration state | tracked in `schema_migrations`; prod applied through 015+ |

## 1. Two access methods (pick per task)

**A. docker-exec (passwordless, via ssh trust) — for ad-hoc SQL / piping a file.**
```bash
ssh -o BatchMode=yes -p 21488 sasha@158.69.112.39 \
  'docker exec -i lustdex-db psql -U catalog -d catalog -v ON_ERROR_STOP=1'  # SQL on stdin
```
Use for: read-only inspection, piping a migration `.sql`, one-shot INSERT/ANALYZE.
No tunnel, no password (psql trust inside the container).

**B. tunnel + DATABASE_URL → run the real Python (migrate.py / refresh.py).**
Preferred when you want the *same code* as local to run against prod (single source
of truth), e.g. recurring `pipeline.*.refresh`.
```bash
ssh -fN -L 5433:127.0.0.1:5432 -p 21488 sasha@158.69.112.39      # background tunnel
DATABASE_URL='postgresql://catalog:<PW>@127.0.0.1:5433/catalog?sslmode=disable' \
  .venv/bin/python -m db.migrate        # or -m pipeline.projections.refresh
pkill -f "ssh.*5433.*21488"                                       # tear down
```
The prod password lives in `.claude/settings.local.json` (gitignored). Tunnel port
**5433** by convention (not 15432).

## 2. Decision tree — what am I deploying?

**Migration (DDL)** → apply on prod. Either:
- B: `DATABASE_URL→prod python -m db.migrate` (keeps `schema_migrations` correct
  automatically), or
- A: pipe the `.sql` + append `INSERT INTO schema_migrations (version) VALUES ('NNN_slug');`,
  all under `psql -1 -v ON_ERROR_STOP=1` (one transaction).

**Data** → classify first:
- **Derived / deterministic** (projection rollups, counts, tag denorm — a pure
  function of `cat.*` already on prod): **rebuild ON prod**, do not ship local rows.
  Shipping local rows is WRONG — they reference local video_ids that don't exist on
  prod (prod is smaller). Run the refresh against prod (method B), or pipe the
  equivalent `INSERT … SELECT` (method A).
- **Authored / LLM / local-only-input** (performer gender, curation, semantic-page
  snapshots): **build LOCAL → deploy** via `pg_dump --data-only` + `TRUNCATE … CASCADE`
  + restore (the performers pattern). NEVER compute these on prod. This is the
  build-local→deploy-prod invariant: prod stays a deployed copy so a reset can't lose
  authored work.

Litmus: *"Could prod recompute this from its own `cat.*` deterministically?"* Yes →
refresh on prod. No (needs an LLM call / human curation / data only on local) →
pg_dump deploy.

## 3. Loading into a table: first load vs live refresh

- **First load** (table just created empty by a migration, nothing reads it yet —
  e.g. app §7 not shipped): direct `INSERT … SELECT` into the real table. No swap.
- **Recurring refresh of a table the live site reads**: **shadow + atomic swap** —
  build `*_new`, index it, then `BEGIN; DROP old; ALTER … RENAME; COMMIT`. Never
  `TRUNCATE` a live table (empties it under reads). See `pipeline/projections/refresh.py`.

## 4. Always, after a bulk load

- `ANALYZE cat.<table>;` — fresh tables have no stats; the planner will misplan
  without it.
- **Verify against ground-truth.** For projection rollups, compare with the FULL
  projection rule (spec 06 §4): `(vertical = ANY(from_verticals) OR EXISTS include)
  AND NOT EXISTS exclude`. A naive `vertical='trans'`-only check is WRONG (misses
  non-trans videos pulled in by `include_tag_ids`) and will look like a rollup bug
  when it is the check that is wrong. (Recorded burn.)

## 5. Safety

- Prod is **live**. Confirm destructive ops (`TRUNCATE`, `DROP`, `DELETE`) with the
  user first; prefer additive / swap patterns.
- Be aware of backups before destructive data deploys (TODO: backup story, MVP #5).
- `ssh -o BatchMode=yes` first to confirm key auth without hanging on a prompt.
- Treat the prod password as a secret — never echo it into logs or commits.

## 6. Keep this current

When the prod host, container name, port, access method, or a deploy nuance changes,
**edit this file**. It is the single source of prod-ops truth; stale facts here cause
the exact mistakes it exists to prevent.
