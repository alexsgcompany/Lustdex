"""Ingest pipeline for zilla.cash feeds (around-xxx, analdin-com)."""

import argparse
from datetime import datetime

import httpx
from psycopg.types.json import Jsonb

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)

PROVIDER_SLUG = "zilla-cash"
PROVIDER_NAME = "zilla.cash"

_SITES = [
    {"slug": "around-xxx", "domain": "around.xxx"},
    {"slug": "analdin-com", "domain": "analdin.com"},
]

_FEEDS = [
    {
        "site_slug": "around-xxx",
        "feed_url": "https://zilla.cash/around/feed/?limit={limit}",
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "mix",
        "sub_niche": "mix",
    },
    {
        "site_slug": "analdin-com",
        "feed_url": (
            "https://www.analdin.com/admin/feeds/default/"
            "?feed_format=csv&limit={limit}&screenshot_format=source"
            "&csv_separator=%7C"
            "&csv_columns=id%7Clink%7Ctitle%7Cdescription%7Ccategories"
            "%7Cmodels%7Cduration%7Cpost_date%7Cmain_screenshot%7Cembed%7Ccustom1"
        ),
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "mix",
        "sub_niche": "mix",
    },
]

# --- helpers ---

def _split(val: str) -> list[str]:
    return [v.strip() for v in val.split(",") if v.strip()]

def _int(val: str) -> int | None:
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return None

def _str(val: str) -> str | None:
    v = val.strip() if val else ""
    return v or None

def _dt(val: str) -> datetime | None:
    if v := _str(val):
        try:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None

# --- row mappers ---

def _map_around_xxx(cols: list[str]) -> dict:
    payload: dict = {}
    if u := _str(cols[9]):
        payload["uploader"] = u
    if q := _str(cols[10]):
        payload["quality"] = q
    return {
        "external_id":    cols[0].strip(),
        "target_url":     _str(cols[1]),
        "title":          _str(cols[2]),
        "description":    None,
        "tags_raw":       _split(cols[3]),
        "performers_raw": _split(cols[4]),
        "duration_sec":   _int(cols[5]),
        "published_at":   None,
        "thumb_url":      _str(cols[7]),
        "payload":        Jsonb(payload),
    }

def _map_analdin(cols: list[str]) -> dict:
    payload: dict = {}
    if e := _str(cols[9]):
        payload["embed"] = e
    if q := _str(cols[10]):
        payload["quality"] = q
    return {
        "external_id":    cols[0].strip(),
        "target_url":     _str(cols[1]),
        "title":          _str(cols[2]),
        "description":    _str(cols[3]),
        "tags_raw":       _split(cols[4]),
        "performers_raw": _split(cols[5]),
        "duration_sec":   _int(cols[6]),
        "published_at":   _dt(cols[7]),
        "thumb_url":      _str(cols[8]),
        "payload":        Jsonb(payload),
    }

_MAPPERS: dict[str, tuple] = {
    "around-xxx": (_map_around_xxx, 11),
    "analdin-com": (_map_analdin, 11),
}

# --- DB seed ---

def _seed_db(conn) -> tuple[int, dict[str, int]]:
    """Upsert provider/sites/feeds. Returns (provider_id, {site_slug: feed_id})."""
    provider_id = conn.execute(
        """
        INSERT INTO cat.providers (slug, name) VALUES (%s, %s)
        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (PROVIDER_SLUG, PROVIDER_NAME),
    ).fetchone()[0]

    site_ids: dict[str, int] = {}
    for s in _SITES:
        site_ids[s["slug"]] = conn.execute(
            """
            INSERT INTO raw.sites (provider_id, slug, domain) VALUES (%s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET domain = EXCLUDED.domain
            RETURNING id
            """,
            (provider_id, s["slug"], s["domain"]),
        ).fetchone()[0]

    feed_ids: dict[str, int] = {}
    for f in _FEEDS:
        feed_ids[f["site_slug"]] = conn.execute(
            """
            INSERT INTO raw.feeds
                (site_id, feed_url, feed_format, has_header, max_limit, niche, sub_niche)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (site_id) DO UPDATE SET
                feed_url  = EXCLUDED.feed_url,
                max_limit = EXCLUDED.max_limit
            RETURNING id
            """,
            (
                site_ids[f["site_slug"]], f["feed_url"], f["feed_format"],
                f["has_header"], f["max_limit"], f["niche"], f["sub_niche"],
            ),
        ).fetchone()[0]

    conn.commit()
    return provider_id, feed_ids

# --- ingest ---

_UPSERT_SQL = """
INSERT INTO raw.raw_videos (
    provider_id, feed_id, external_id, title, description,
    duration_sec, target_url, thumb_url, tags_raw, performers_raw,
    published_at, payload
) VALUES (
    %(provider_id)s, %(feed_id)s, %(external_id)s, %(title)s, %(description)s,
    %(duration_sec)s, %(target_url)s, %(thumb_url)s, %(tags_raw)s, %(performers_raw)s,
    %(published_at)s, %(payload)s
)
ON CONFLICT (feed_id, external_id) DO UPDATE SET
    title          = EXCLUDED.title,
    description    = EXCLUDED.description,
    duration_sec   = EXCLUDED.duration_sec,
    target_url     = EXCLUDED.target_url,
    thumb_url      = EXCLUDED.thumb_url,
    tags_raw       = EXCLUDED.tags_raw,
    performers_raw = EXCLUDED.performers_raw,
    published_at   = EXCLUDED.published_at,
    payload        = EXCLUDED.payload,
    fetched_at     = now()
"""

def _ingest_feed(conn, feed_cfg: dict, provider_id: int, feed_id: int, limit: int) -> None:
    url = feed_cfg["feed_url"].format(limit=limit)
    mapper, expected_cols = _MAPPERS[feed_cfg["site_slug"]]

    log.info("fetch  %s  limit=%d", feed_cfg["site_slug"], limit)
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()

    lines = resp.text.splitlines()
    if feed_cfg["has_header"] and lines:
        lines = lines[1:]

    rows, errors = [], 0
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            cols = line.split("|")
            if len(cols) != expected_cols:
                raise ValueError(f"expected {expected_cols} cols, got {len(cols)}")
            row = mapper(cols)
            row["provider_id"] = provider_id
            row["feed_id"] = feed_id
            rows.append(row)
        except Exception as e:
            log.warning("row %d: %s", i, e)
            errors += 1

    if rows:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
        conn.commit()

    log.info("done   %s  +%d rows, %d errors", feed_cfg["site_slug"], len(rows), errors)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest zilla.cash feeds")
    parser.add_argument("--limit", type=int, default=None, help="override max_limit")
    args = parser.parse_args()

    with get_conn() as conn:
        provider_id, feed_ids = _seed_db(conn)
        for feed_cfg in _FEEDS:
            feed_id = feed_ids[feed_cfg["site_slug"]]
            limit = args.limit or feed_cfg["max_limit"]
            _ingest_feed(conn, feed_cfg, provider_id, feed_id, limit)


if __name__ == "__main__":
    main()
