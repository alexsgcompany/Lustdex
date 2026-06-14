"""Demo catalog — browse tags and videos.

Run from project root:
    python demo/app.py
"""

import os
import sys
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, abort, render_template, request

import pipeline.common.config  # noqa: F401 — loads DATABASE_URL from .env
from pipeline.common.db import get_conn

app = Flask(__name__)

PER_PAGE = 120  # 6 columns × 20 rows
CDN_BASE = os.environ.get("BUNNY_PULL_ZONE_URL", "").rstrip("/")

PROJECTION_SLUG = os.environ.get("DEMO_PROJECTION", "trans")

with get_conn() as _conn:
    _proj = _conn.execute(
        "SELECT slug, from_verticals, include_tag_ids, exclude_tag_ids, brand_tag_id"
        "  FROM cat.projections WHERE slug = %s AND active",
        (PROJECTION_SLUG,),
    ).fetchone()
if not _proj:
    raise RuntimeError(f"projection {PROJECTION_SLUG!r} not found or inactive")
PROJECTION = {
    "slug":            _proj[0],
    "from_verticals":  _proj[1],
    "include_tag_ids": _proj[2],
    "exclude_tag_ids": _proj[3],
    "brand_tag_id":    _proj[4],
}

# Spec 07 §4 projection WHERE snippet (videos table MUST be aliased as `v`).
PROJ_WHERE = """(
    v.vertical = ANY(%s::text[])
    OR EXISTS (SELECT 1 FROM cat.video_tags vt
               WHERE vt.video_id = v.id AND vt.tag_id = ANY(%s::int[]))
) AND NOT EXISTS (SELECT 1 FROM cat.video_tags vt
                  WHERE vt.video_id = v.id AND vt.tag_id = ANY(%s::int[]))"""
PROJ_BINDS = (
    PROJECTION["from_verticals"],
    PROJECTION["include_tag_ids"],
    PROJECTION["exclude_tag_ids"],
)


def _thumb_url(cdn_path: str | None, raw_url: str | None) -> str | None:
    if cdn_path and CDN_BASE:
        return f"{CDN_BASE}/{cdn_path}"
    return raw_url


@app.route("/")
def index():
    brand_id = PROJECTION["brand_tag_id"]
    with get_conn() as conn:
        tag_rows = conn.execute(
            f"""
            SELECT t.id, t.name, t.slug, t.category, COUNT(v.id) AS cnt
            FROM cat.tags t
            JOIN cat.video_tags vt ON vt.tag_id = t.id
            JOIN cat.videos v ON v.id = vt.video_id
            WHERE t.status = 'active'
              AND {PROJ_WHERE}
            GROUP BY t.id, t.name, t.slug, t.category
            HAVING COUNT(v.id) > 0
            ORDER BY t.category NULLS LAST, t.name
            """,
            PROJ_BINDS,
        ).fetchall()

        candidate_rows = conn.execute(
            """
            SELECT pc.slug_provisional, pc.slug_final, pc.lexical_count,
                   array_agg(t.name ORDER BY t.name) AS tag_names
            FROM cat.page_candidates pc
            JOIN cat.tags t ON t.id = ANY(pc.member_tag_ids)
            WHERE pc.status = 'approved'
              AND (%s::int IS NULL OR %s = ANY(pc.member_tag_ids))
            GROUP BY pc.id, pc.slug_provisional, pc.slug_final, pc.lexical_count
            ORDER BY pc.lexical_count DESC
            """,
            (brand_id, brand_id),
        ).fetchall()

    groups: dict[str, list] = {}
    for row in tag_rows:
        cat = row[3] or "Other"
        groups.setdefault(cat, []).append({"name": row[1], "slug": row[2], "cnt": row[4]})

    candidates = [
        {"slug": r[1] or r[0], "slug_prov": r[0], "cnt": r[2], "tags": r[3]}
        for r in candidate_rows
    ]

    return render_template("index.html", groups=groups, candidates=candidates)


@app.route("/p/<slug>")
def candidate_page(slug: str):
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PER_PAGE
    brand_id = PROJECTION["brand_tag_id"]

    with get_conn() as conn:
        candidate = conn.execute(
            """
            SELECT pc.id, pc.slug_provisional, pc.slug_final, pc.lexical_count,
                   pc.member_tag_ids,
                   array_agg(t.name ORDER BY t.name) AS tag_names
            FROM cat.page_candidates pc
            JOIN cat.tags t ON t.id = ANY(pc.member_tag_ids)
            WHERE pc.slug_provisional = %s
              AND (%s::int IS NULL OR %s = ANY(pc.member_tag_ids))
            GROUP BY pc.id, pc.slug_provisional, pc.slug_final, pc.lexical_count, pc.member_tag_ids
            """,
            (slug, brand_id, brand_id),
        ).fetchone()
        if not candidate:
            abort(404)

        tag_ids = candidate[4]
        n = len(tag_ids)

        total = conn.execute(
            f"""
            SELECT COUNT(*) FROM cat.videos v
            WHERE v.id IN (
                SELECT video_id FROM cat.video_tags
                WHERE tag_id = ANY(%s)
                GROUP BY video_id
                HAVING COUNT(DISTINCT tag_id) = %s
            )
              AND {PROJ_WHERE}
            """,
            (tag_ids, n, *PROJ_BINDS),
        ).fetchone()[0]

        videos = conn.execute(
            f"""
            WITH matched AS (
                SELECT video_id FROM cat.video_tags
                WHERE tag_id = ANY(%s)
                GROUP BY video_id
                HAVING COUNT(DISTINCT tag_id) = %s
            )
            SELECT v.title, va.path, rv.thumb_url, v.target_url
            FROM cat.videos v
            JOIN raw.raw_videos rv ON rv.id = v.id
            JOIN matched m ON m.video_id = v.id
            LEFT JOIN cat.video_assets va
                   ON va.video_id = v.id AND va.kind = 'thumb'
            WHERE {PROJ_WHERE}
            ORDER BY v.id
            LIMIT %s OFFSET %s
            """,
            (tag_ids, n, *PROJ_BINDS, PER_PAGE, offset),
        ).fetchall()

    pages = ceil(total / PER_PAGE) if total else 1
    return render_template(
        "candidate.html",
        candidate={
            "slug": candidate[2] or candidate[1],
            "slug_prov": candidate[1],
            "cnt": candidate[3],
            "tags": candidate[5],
        },
        videos=[{"title": v[0], "thumb": _thumb_url(v[1], v[2]), "url": v[3]} for v in videos],
        page=page,
        pages=pages,
        total=total,
    )


@app.route("/<slug>")
def tag_page(slug: str):
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PER_PAGE

    with get_conn() as conn:
        tag = conn.execute(
            "SELECT id, name, slug, category FROM cat.tags WHERE slug = %s AND status = 'active'",
            (slug,),
        ).fetchone()
        if not tag:
            abort(404)

        total = conn.execute(
            f"""
            SELECT COUNT(*) FROM cat.videos v
            JOIN cat.video_tags vt ON vt.video_id = v.id
            WHERE vt.tag_id = %s
              AND {PROJ_WHERE}
            """,
            (tag[0], *PROJ_BINDS),
        ).fetchone()[0]

        videos = conn.execute(
            f"""
            SELECT v.title, va.path, rv.thumb_url, v.target_url
            FROM cat.videos v
            JOIN raw.raw_videos rv ON rv.id = v.id
            JOIN cat.video_tags vt ON vt.video_id = v.id
            LEFT JOIN cat.video_assets va
                   ON va.video_id = v.id AND va.kind = 'thumb'
            WHERE vt.tag_id = %s
              AND {PROJ_WHERE}
            ORDER BY v.id
            LIMIT %s OFFSET %s
            """,
            (tag[0], *PROJ_BINDS, PER_PAGE, offset),
        ).fetchall()

    pages = ceil(total / PER_PAGE) if total else 1
    return render_template(
        "tag.html",
        tag={"id": tag[0], "name": tag[1], "slug": tag[2], "category": tag[3]},
        videos=[{"title": v[0], "thumb": _thumb_url(v[1], v[2]), "url": v[3]} for v in videos],
        page=page,
        pages=pages,
        total=total,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
