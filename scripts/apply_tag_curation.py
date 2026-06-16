"""Apply a curated batch of tag decisions: create canonicals, alias, mark resolved/trash.

Edit the three constants below for each batch. Re-runnable (idempotent).
"""

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)

# (slug, name, category, [normalized aliases that point here])
NEW_CANONICALS: list[tuple[str, str, str, list[str]]] = [
    ("hd",                 "HD",                 "quality", ["hd", "premium hd", "hd video"]),
    ("ladyboy",            "Ladyboy",            "role",    ["ladyboy"]),
    ("crossdresser",       "Crossdresser",       "role",    ["crossdresser", "cd", "crossdressing"]),
    ("sissy",              "Sissy",              "role",    ["sissy"]),
    ("femboy",             "Femboy",             "role",    ["femboy"]),
    ("tgirl",              "Tgirl",              "role",    ["tgirl"]),
    ("shemale-and-female", "Shemale and Female", "act",     ["shemale and girl", "shemale fucks female"]),
    ("ass-to-mouth",       "Ass to Mouth",       "act",     ["ass to mouth"]),
    ("small-cock",         "Small Cock",         "body",    ["small cock"]),
    ("frottage",           "Frottage",           "act",     ["frottage"]),
    ("slim",               "Slim",               "body",    ["slim shemale"]),
]

# (normalized_key, existing_canonical_slug)
ALIASES_TO_EXISTING: list[tuple[str, str]] = [
    ("solo shemale",         "solo"),
    ("anal sex",             "anal"),
    ("shemale shemale",      "shemale-fucks-shemale"),
    ("cam",                  "webcam"),
    ("tran",                 "shemale"),
    ("transsexual",          "shemale"),
    ("jerk off",             "masturbation"),
    ("sucking",              "blowjob"),
    ("small boob",           "small-tits"),
    ("transgender",          "shemale"),
    ("tranny solo",          "solo"),
    ("suck",                 "blowjob"),
    ("jerking",              "masturbation"),
    ("male fucks shemale",   "guy-fucks-shemale"),
    ("masturbate",           "masturbation"),
    ("bigtit",               "big-tits"),
    ("ass fuck",             "ass-fucking"),
    ("guy on shemale",       "guy-fucks-shemale"),
    ("tscamlive",            "webcam"),
    ("masturbating",         "masturbation"),
    ("bigcock",              "big-dick"),
    ("guyonshemale",         "guy-fucks-shemale"),
    ("alone",                "solo"),
    ("guy fucks tranny",     "guy-fucks-shemale"),
    ("shemale masturbation", "masturbation"),
    ("tattooed tgirl",       "tattoo"),
    ("oral sex",             "blowjob"),
    ("live",                 "webcam"),
    ("brunette t",           "brunette"),
    ("sexy shemale",         "shemale"),
]

TRASH: list[str] = ["t", "creator", "fuck", "cock"]


_ALIAS_UPSERT = """
INSERT INTO cat.tag_aliases (normalized, tag_id, source, confidence)
VALUES (%s, %s, 'manual', 1.0)
ON CONFLICT (normalized) DO UPDATE SET
    tag_id     = EXCLUDED.tag_id,
    source     = 'manual',
    confidence = 1.0
"""


def main() -> None:
    with get_conn() as conn:
        # 1. New canonicals
        slug_to_id: dict[str, int] = {}
        for slug, name, category, _ in NEW_CANONICALS:
            (tag_id,) = conn.execute(
                """
                INSERT INTO cat.tags (slug, name, category)
                VALUES (%s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, category = EXCLUDED.category
                RETURNING id
                """,
                (slug, name, category),
            ).fetchone()
            slug_to_id[slug] = tag_id
            log.info("canonical  %-22s id=%d  cat=%s", slug, tag_id, category)

        # 2. Aliases for new canonicals
        new_aliases = 0
        for slug, _, _, aliases in NEW_CANONICALS:
            for norm in aliases:
                conn.execute(_ALIAS_UPSERT, (norm, slug_to_id[slug]))
                new_aliases += 1

        # 3. Aliases to existing canonicals
        existing_aliases = 0
        missing: list[str] = []
        for norm, target_slug in ALIASES_TO_EXISTING:
            row = conn.execute("SELECT id FROM cat.tags WHERE slug = %s", (target_slug,)).fetchone()
            if not row:
                missing.append(target_slug)
                continue
            conn.execute(_ALIAS_UPSERT, (norm, row[0]))
            existing_aliases += 1
        if missing:
            raise SystemExit(f"missing canonical target slugs: {missing}")

        # 4. Mark resolved (everything we just aliased)
        resolved_keys = [n for _, _, _, aliases in NEW_CANONICALS for n in aliases] + \
                        [n for n, _ in ALIASES_TO_EXISTING]
        conn.execute(
            "UPDATE raw.unmapped_tags SET status='resolved', updated_at=now() "
            "WHERE normalized = ANY(%s)",
            (resolved_keys,),
        )

        # 5. Mark trash
        conn.execute(
            "UPDATE raw.unmapped_tags SET status='trash', updated_at=now() "
            "WHERE normalized = ANY(%s)",
            (TRASH,),
        )

        conn.commit()

        (pending_left,) = conn.execute(
            "SELECT count(*) FROM raw.unmapped_tags WHERE status='pending'"
        ).fetchone()
        log.info("--")
        log.info("new canonicals:        %d", len(NEW_CANONICALS))
        log.info("aliases (new-canon):   %d", new_aliases)
        log.info("aliases (existing):    %d", existing_aliases)
        log.info("resolved keys:         %d", len(resolved_keys))
        log.info("trash keys:            %d", len(TRASH))
        log.info("pending left in queue: %d", pending_left)
        log.info("--")
        log.info("next: python -m pipeline.tags.apply")


if __name__ == "__main__":
    main()
