"""One-off: import a filled dict_candidates.csv into tags + tag_aliases.

CSV columns (add to seed_dictionary output before running):
  normalized   — from seed_dictionary (key)
  slug         — canonical plural slug, e.g. "big-tits"  ← fill this
  name         — display name, e.g. "Big Tits"           ← optional, derived from slug if empty
  category     — e.g. "modifier"                         ← optional
  freq/raw_examples — ignored

Rows with empty slug are skipped.
Each imported row: upserts tags, writes tag_aliases (source=manual, confidence=1.0),
marks unmapped_tags as resolved.

Run: python scripts/import_dictionary.py dict_candidates.csv
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file")
    args = parser.parse_args()

    with open(args.csv_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    imported = skipped = 0
    with get_conn() as conn:
        for row in rows:
            slug = (row.get("slug") or row.get("canonical_slug") or "").strip()
            if not slug:
                skipped += 1
                continue

            normalized = row["normalized"].strip()
            name = (row.get("name") or "").strip() or slug.replace("-", " ").title()
            category = row.get("category", "").strip() or None

            tag_id = conn.execute(
                """
                INSERT INTO tags (slug, name, category)
                VALUES (%s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, category = EXCLUDED.category
                RETURNING id
                """,
                (slug, name, category),
            ).fetchone()[0]

            conn.execute(
                """
                INSERT INTO tag_aliases (normalized, tag_id, source, confidence)
                VALUES (%s, %s, 'manual', 1.0)
                ON CONFLICT (normalized) DO NOTHING
                """,
                (normalized, tag_id),
            )

            conn.execute(
                "UPDATE unmapped_tags SET status = 'resolved', updated_at = now() WHERE normalized = %s",
                (normalized,),
            )

            imported += 1

        conn.commit()

    log.info("imported=%d  skipped=%d", imported, skipped)


if __name__ == "__main__":
    main()
