"""All SQL for the Tags admin section. No pipeline logic here."""

# Coverage by occurrences.
# collect.py only updates freq in raw.unmapped_tags for pending/trash rows; resolved rows keep
# stale freq from the run that first inserted them. To avoid that skew, we derive total_occ
# from the raw source (sum of array_length over raw.raw_videos.tags_raw) and compute resolved as
# total minus the pending and trash freq — both of which are always current after every collect.
COVERAGE = """
SELECT
    raw.total_occ                                   AS total_occ,
    raw.total_occ - pt.pending_trash_freq           AS resolved_occ
FROM
    (SELECT SUM(array_length(tags_raw, 1)) AS total_occ
     FROM raw.raw_videos WHERE tags_raw IS NOT NULL)    raw,
    (SELECT COALESCE(SUM(freq), 0)         AS pending_trash_freq
     FROM raw.unmapped_tags WHERE status IN ('pending', 'trash')) pt
"""

COUNTERS = """
SELECT
    (SELECT COUNT(*)   FROM cat.tags)                                 AS canonical_total,
    (SELECT COUNT(*)   FROM cat.tag_aliases)                          AS aliases_total,
    (SELECT COUNT(*)   FROM raw.unmapped_tags WHERE status = 'pending') AS queue_pending,
    (SELECT COUNT(*)   FROM raw.unmapped_tags WHERE status = 'trash')   AS queue_trash
"""

ORPHAN_COUNT = """
SELECT COUNT(*)
FROM raw.raw_videos rv
WHERE NOT EXISTS (SELECT 1 FROM cat.video_tags vt WHERE vt.video_id = rv.id)
"""

ORPHAN_LIST = """
SELECT rv.id, rv.title, rv.tags_raw
FROM raw.raw_videos rv
WHERE NOT EXISTS (SELECT 1 FROM cat.video_tags vt WHERE vt.video_id = rv.id)
ORDER BY rv.id
LIMIT 100
"""

CATEGORY_DISTRIBUTION = """
SELECT COALESCE(category, '(none)') AS category, COUNT(*) AS n
FROM cat.tags
GROUP BY 1
ORDER BY n DESC
"""

REVIEW_QUEUE = """
SELECT
    ut.normalized,
    ut.freq,
    ut.raw_examples,
    ut.suggested_tag_id,
    ut.suggested_source,
    ut.suggested_confidence,
    t.slug  AS suggested_slug,
    t.name  AS suggested_name
FROM raw.unmapped_tags ut
LEFT JOIN cat.tags t ON t.id = ut.suggested_tag_id
WHERE ut.status = 'pending'
  {provider_clause}
  {suggestion_clause}
  {freq_clause}
ORDER BY ut.freq DESC
"""

CANONICAL_SEARCH = """
SELECT id, slug, name
FROM cat.tags
WHERE (slug ILIKE %s OR name ILIKE %s)
ORDER BY name
LIMIT 30
"""

DISTINCT_CATEGORIES = """
SELECT DISTINCT category FROM cat.tags WHERE category IS NOT NULL ORDER BY 1
"""

DICTIONARY_LIST = """
SELECT
    t.id,
    t.slug,
    t.name,
    t.category,
    COALESCE(SUM(ut.freq), 0) AS occ_freq
FROM cat.tags t
LEFT JOIN cat.tag_aliases ta ON ta.tag_id = t.id
LEFT JOIN raw.unmapped_tags ut ON ut.normalized = ta.normalized
{where}
GROUP BY t.id
ORDER BY occ_freq DESC
"""

TAG_ALIASES = """
SELECT normalized, source, confidence
FROM cat.tag_aliases
WHERE tag_id = %s
ORDER BY confidence DESC, normalized
"""

TAG_SAMPLE_VIDEOS = """
SELECT rv.id, rv.title
FROM cat.video_tags vt
JOIN raw.raw_videos rv ON rv.id = vt.video_id
WHERE vt.tag_id = %s
LIMIT 5
"""

PIPELINE_RUNS = """
SELECT
    id,
    job,
    status,
    started_at,
    finished_at,
    EXTRACT(EPOCH FROM (COALESCE(finished_at, now()) - started_at))::int AS duration_sec,
    stats,
    error
FROM raw.pipeline_runs
ORDER BY started_at DESC
LIMIT 50
"""

RUNNING_JOBS = """
SELECT DISTINCT job FROM raw.pipeline_runs WHERE status = 'running'
"""
