"""Dump top-N pending keys by frequency for building the canonical tag dictionary.

Run:  python -m pipeline.tags.seed_dictionary [--top N] [--output file.csv]
"""

import argparse
import csv
import sys

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump top-N pending tag keys")
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--output", default="-", help="output CSV path, or - for stdout")
    args = parser.parse_args()

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT normalized, freq, raw_examples
            FROM raw.unmapped_tags
            WHERE status = 'pending'
            ORDER BY freq DESC
            LIMIT %s
            """,
            (args.top,),
        ).fetchall()

    out = open(args.output, "w", newline="") if args.output != "-" else sys.stdout
    try:
        w = csv.writer(out)
        w.writerow(["normalized", "freq", "raw_examples"])
        for normalized, freq, examples in rows:
            w.writerow([normalized, freq, "|".join(examples or [])])
    finally:
        if args.output != "-":
            out.close()

    log.info("wrote %d rows", len(rows))


if __name__ == "__main__":
    main()
