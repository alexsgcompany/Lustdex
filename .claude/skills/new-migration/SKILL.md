---
name: new-migration
description: Scaffold the next numbered SQL migration in db/migrations/ with the project header style. Use when adding a schema change. Computes the next version number, writes a template, and reminds of the append-only / no-hardcoded-ID rules.
disable-model-invocation: true
---

# new-migration

Create the next append-only migration in `db/migrations/`.

## Steps

1. **Find the next number.** List `db/migrations/NNN_*.sql`, take the highest
   `NNN`, add 1, zero-pad to 3 digits. Never reuse or renumber existing files.

2. **Pick a slug** from the user's description: lowercase, words separated by
   `_` (e.g. `add_video_rating`). Filename: `NNN_<slug>.sql`.

3. **Write the file** with this header style (matches existing migrations):

   ```sql
   -- NNN_<slug>.sql — <one-line purpose> (spec NN)
   --
   -- <2-4 lines: what tables/columns, why, which spec section. Note if it is
   -- deterministic from raw.* (runnable directly on prod) or authored locally.>

   <plain DDL — CREATE TABLE / ALTER TABLE / CREATE INDEX>
   ```

   - Plain DDL, **no** `IF NOT EXISTS`. `migrate.py` runs each version exactly
     once inside a transaction, tracked in `schema_migrations` — guards are
     redundant and off-style here.
   - Match the column formatting of `015_performers.sql` (aligned types,
     inline `CHECK (...)` constraints, `created_at timestamptz NOT NULL DEFAULT now()`).

4. **Stop and confirm decisions, don't pick silently** (CLAUDE.md §1, §5):
   thresholds, types, on-delete behavior, new tables — propose, let the user
   approve before writing.

## Hard rules (FOUNDATION.md)

- **Append-only.** New file only. Never edit an applied (committed) migration —
  a PreToolUse hook enforces this.
- **No ORM / Alembic.** Plain SQL via psycopg3 only.
- **No DDL + UPDATE-with-hardcoded-IDs in one migration.** It breaks a fresh DB
  where those IDs don't exist yet. Split: DDL in the migration, data backfill in
  a separate pipeline script (idempotent, upsert). This is a recorded past burn.
- **Raw data immutable.** Schema for `raw_*` tables is structural only; fixes
  live as mappings on top (e.g. `tag_aliases`), never as edits to raw rows.

## After writing

Apply with: `.venv/bin/python -m db.migrate` (needs `DATABASE_URL` from `.env`).
Keep the file **uncommitted** while iterating; commit only once the shape is
final (commit = "applied", locked by the hook).
