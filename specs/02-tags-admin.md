# specs/02-tags-admin.md — Admin: Tags section (Streamlit)

Scope: local Streamlit admin, Tags section only. Four screens: Overview, Review, Dictionary, Runs.
First iteration = single-item actions only. Bulk ops and Dictionary merge are explicitly DEFERRED (see §7).

## 0. Principles (non-negotiable)

1. **Admin contains NO pipeline logic.** It reads the DB for display, writes only review decisions (aliases, tag rows, statuses). Running a pipeline step = `subprocess.Popen` of the existing CLI (`python -m pipeline.tags.<job>`), never importing/reimplementing it.
2. **DB access goes through `pipeline/common/db.py`** (`get_conn`). No second connection layer, no ORM.
3. **Verify the real schema before writing queries.** Column names below are from the spec; the actual migrations may differ. Read `db/migrations/*.sql` first and map these requirements onto real columns. Do NOT assume.
4. Long-running jobs must not block the UI — fire-and-forget Popen, status read back from `pipeline_runs`, manual refresh button.

## 1. Location & structure

```
admin/
  app.py            — Streamlit entry, sidebar nav, calls get_conn
  tags/
    overview.py     — screen 1
    review.py       — screen 2
    dictionary.py   — screen 3
    runs.py         — screen 4
    queries.py      — all SQL for the Tags section, one place
```

Run: `streamlit run admin/app.py`. Reads `DATABASE_URL` from `.env` via existing config loader.
New dependency (approved): `streamlit`.

## 2. Screen — Overview (read-only dashboard)

Purpose: current state at a glance. No actions.

- **Headline metric: coverage by occurrences** — % of all tag occurrences in `raw_videos.tags_raw` that resolve to a canonical tag (i.e. normalized key exists in `tag_aliases`). NOT % of unique tags. This is THE number that says "can I build pages yet".
  - Compute: total occurrences vs occurrences whose normalized key has an alias. If a cheaper proxy via `video_tags` vs raw counts is accurate, that's fine — decide based on real schema, document the choice in a code comment.
- Counters: canonical tags total, aliases total, queue pending, trash.
- **Orphan videos**: count of `raw_videos` with zero rows in `video_tags`. Show count + expandable list (id, title, raw tags). This list literally says "resolve these queued tags and N videos come alive".
- Canonical distribution by `category` (simple table or bar: category → count). Reveals dictionary skew.

All numbers are queries in `queries.py`. No caching needed at this scale; a refresh button is enough.

## 3. Screen — Review (the main workspace)

Queue = `unmapped_tags WHERE status='pending'`, sorted `freq DESC` (highest-impact first).

Per row, display: normalized key · freq · raw_examples · suggestion (if `suggested_tag_id` set: canonical name + source + confidence).

Single-item actions (semantics from specs/01-tags.md §3.3):
- **Accept suggestion** (shown only if a suggestion exists) → insert `tag_aliases(normalized, suggested_tag_id, source=suggested_source, confidence=suggested_confidence)`; set row `status='resolved'`.
- **Map to other** → searchable select over canonical tags (typeahead by name/slug) → alias `source='manual'`, confidence 1.0; row `resolved`.
- **New canonical** → inline inputs: slug (required, validate: lowercase, hyphenated, unique), name, category (select from existing categories + free text) → insert into `tags`, then alias `source='manual'`; row `resolved`.
- **Trash** → row `status='trash'`, no alias.

Filters (top of screen): by provider (uses `unmapped_tags.providers`), has-suggestion (yes/no/any), freq range (min slider).

After any action: re-query the queue so the row disappears. One row = one decision; no multi-select in this iteration.

Note: resolving an alias does NOT auto-create `video_tags` rows — that requires re-running Apply (Runs screen). Surface this: after N resolutions, a hint "run Apply to link videos".

## 4. Screen — Dictionary (canonical view + light edit)

Table of all canonical `tags`: slug, name, category, and **occurrence freq** (sum of freq of aliases pointing to it — reveals dead canonicals created but unused).
- Search by name/slug, filter by category.
- Click a tag → detail: its aliases (normalized + source + confidence) and a few sample video titles via `video_tags`.
- Edit `category` and `name` inline. Editing `slug` allowed but warn (it's a URL); must stay unique.

DEFERRED this iteration: merge two canonicals, delete canonical. (See §7.)

## 5. Screen — Runs (control + observability)

- Buttons: **Collect**, **Cascade**, **Apply** → each `subprocess.Popen(["python","-m","pipeline.tags.<job>"])`, non-blocking. Disable a button while its job shows `running` in `pipeline_runs`.
- Table: recent `pipeline_runs` (latest first): job, status, started/finished, duration, stats (jsonb rendered readable, e.g. "processed 4200 · auto 3100 · queued 800"), error (expandable, full text).
- Refresh button (no auto-poll needed).

## 6. Definition of Done

- `streamlit run admin/app.py` opens; sidebar switches between 4 screens; all read from real DB via get_conn.
- Overview shows coverage-by-occurrence number that matches a hand-checked SQL query.
- Review: each of the 4 actions writes correct rows and the handled item leaves the queue on refresh; filters work.
- Dictionary: list + detail + inline category/name edit persist.
- Runs: each button launches the real CLI; pipeline_runs table reflects it; running job's button is disabled.
- No SQL references a column that doesn't exist in migrations; no pipeline logic duplicated in admin/.

## 7. Deferred (next iteration, separate task — do NOT build now)

- Review: multi-select + bulk trash / bulk accept.
- Dictionary: merge two canonicals into one (rewrites aliases, re-points video_tags), delete canonical.
- Raw diagnostic dump screen (only if Overview's orphan list proves insufficient).