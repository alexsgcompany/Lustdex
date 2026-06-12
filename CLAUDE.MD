# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes, adapted for this project.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

**Project context:** Data pipeline for a network of niche video catalogs. Postgres is the single source of truth; Python scripts process partner feeds, normalize tags, and prepare landing-page candidates. Full spec: `FOUNDATION.md` — read it before any architectural decision. One maintainer, solo workflow — simplicity and recoverability beat cleverness.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan and verify each step.

Session workflow: pick ONE item from `TODO.md`, do it, check it off. Don't drift into adjacent items.

## 5. Project Structure

```
CLAUDE.md               — this file
FOUNDATION.md           — full project spec: stack, schema, principles
TODO.md                 — flat list of next steps; check off what's done
docker-compose.yml      — Postgres (pgvector) + adminer; the ONLY dockerized parts
.env.example            — config template; real .env is gitignored, never committed
pyproject.toml          — uv-managed deps, ruff config
db/migrations/          — numbered SQL files (001_init.sql, ...); append-only
db/migrate.py           — applies migrations in order, tracks schema_migrations
pipeline/common/        — db.py (get_conn), config.py (.env loader), log.py
pipeline/ingest/        — feed parsers; one file = one provider
pipeline/tags/          — tag normalization cascade
pipeline/embeddings/    — vector generation (not started)
pipeline/clustering/    — semantic clustering (not started)
pipeline/llm/           — OpenRouter client, batch runners; prompts/ as .md files
pipeline/gsc/           — Search Console loop (not started)
specs/                  — specs for decision-heavy modules only (tags cascade, clustering)
scripts/                — one-off utilities, not part of the pipeline
data/                   — fully gitignored: raw feeds, temp files
tests/                  — minimal: normalize() and feed parsers
```

Do not create files or modules outside this structure without approval.

For decision-heavy modules (tag cascade, clustering, schema changes): if no spec exists in `specs/`, propose decisions first — don't pick thresholds, algorithms, or schema silently. Simple mechanical code (feed parsers, utilities) needs no spec.

## 6. Hard Project Rules (non-negotiable, from FOUNDATION.md)

1. **Deterministic pipeline only.** Scripts + LLM batch calls with structured JSON output. No agent loops, no runtime LLM decisions inside scripts.
2. **Raw data is immutable.** Feed data lands in `raw_*` tables as-is. Fixes are mappings on top (e.g. `tag_aliases`), never edits to raw rows.
3. **Every pipeline step is idempotent.** Re-running must not break or duplicate data (upsert / ON CONFLICT). State lives in the DB, not in files.
4. **No ORM, no Alembic.** Plain SQL via psycopg3. Schema changes ONLY via a new numbered migration — never edit an applied one.
5. **No new dependencies without explicit approval.** Don't add libraries "just in case".
6. **Config via `.env` only.** No hardcoded paths, keys, or connection strings.
7. **One script = one job**, runnable as `python -m pipeline.<module>.<script>`, progress to stdout.
8. **Python runs locally in venv (uv), never in Docker.** Docker is for Postgres only (GPU access, easier debugging).
9. **LLM batch results are written to the DB with `source` and `confidence` fields.** Prompts live in `pipeline/llm/prompts/` as files, not inline strings.
10. **Batch inserts** via `executemany` / `COPY`, never row-by-row loops.
11. **Feed parse errors don't kill the run:** log, skip the row, report counts at the end.

## 7. Language

The language of the database, code, code comments, prompts, and docs inside the repo is English. Conversation with the user may be in Ukrainian; artifacts are English.