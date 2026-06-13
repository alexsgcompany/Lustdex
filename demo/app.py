"""Demo catalog — browse tags and videos.

Run from project root:
    python demo/app.py
"""

import sys
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline.common.config  # noqa: F401 — loads DATABASE_URL from .env
from flask import Flask, abort, render_template, request
from pipeline.common.db import get_conn

app = Flask(__name__)

PER_PAGE = 120  # 6 columns × 20 rows


@app.route("/")
def index():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.slug, t.category, COUNT(vt.video_id) AS cnt
            FROM tags t
            LEFT JOIN video_tags vt ON vt.tag_id = t.id
            WHERE t.status = 'active'
            GROUP BY t.id, t.name, t.slug, t.category
            ORDER BY t.category NULLS LAST, t.name
            """,
        ).fetchall()

    groups: dict[str, list] = {}
    for row in rows:
        cat = row[3] or "Other"
        groups.setdefault(cat, []).append({"name": row[1], "slug": row[2], "cnt": row[4]})

    return render_template("index.html", groups=groups)


@app.route("/<slug>")
def tag_page(slug: str):
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PER_PAGE

    with get_conn() as conn:
        tag = conn.execute(
            "SELECT id, name, slug, category FROM tags WHERE slug = %s AND status = 'active'",
            (slug,),
        ).fetchone()
        if not tag:
            abort(404)

        total = conn.execute(
            "SELECT COUNT(*) FROM video_tags WHERE tag_id = %s",
            (tag[0],),
        ).fetchone()[0]

        videos = conn.execute(
            """
            SELECT rv.title, rv.thumb_url, rv.target_url
            FROM raw_videos rv
            JOIN video_tags vt ON vt.video_id = rv.id
            WHERE vt.tag_id = %s
            ORDER BY rv.id
            LIMIT %s OFFSET %s
            """,
            (tag[0], PER_PAGE, offset),
        ).fetchall()

    pages = ceil(total / PER_PAGE) if total else 1
    return render_template(
        "tag.html",
        tag={"id": tag[0], "name": tag[1], "slug": tag[2], "category": tag[3]},
        videos=[{"title": v[0], "thumb": v[1], "url": v[2]} for v in videos],
        page=page,
        pages=pages,
        total=total,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
