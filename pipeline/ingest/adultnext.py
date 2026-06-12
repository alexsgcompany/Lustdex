"""Ingest pipeline for adultnext.com feeds (abtranny)."""

import argparse
from datetime import datetime

import httpx
from psycopg.types.json import Jsonb

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)

PROVIDER_SLUG = "adultnext"
PROVIDER_NAME = "adultnext.com"

_SITES = [
    {"slug": "abtranny", "domain": "abtranny.tube"},
]

_FEEDS = [
    {
        "site_slug": "abtranny",
        "feed_url": (
            "https://direct.abtranny.com/feeds/"
            "?link_args=campaign_id:2005374825"
            "&feed_format=csv&limit={limit}&csv_separator=%7C"
        ),
        "feed_format": "csv",
        "has_header": True,
        "max_limit": 999_999_999,
        "niche": "trans",
        "sub_niche": "trans",
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

# --- row mapper ---

def _map_abtranny(cols: list[str]) -> dict:
    # header: ID|Title|Description|Publish date, time|Channel|Website link|
    #         Categories|Models|Duration|Embed code|Main thumbnail|Preview URL
    payload: dict = {}
    if c := _str(cols[4]):
        payload["channel"] = c
    if e := _str(cols[9]):
        payload["embed"] = e
    if p := _str(cols[11]):
        payload["preview_url"] = p
    return {
        "external_id":    cols[0].strip(),
        "title":          _str(cols[1]),
        "description":    _str(cols[2]),
        "published_at":   _dt(cols[3]),
        "target_url":     _str(cols[5]),
        "tags_raw":       _split(cols[6]),
        "performers_raw": _split(cols[7]),
        "duration_sec":   _int(cols[8]),
        "thumb_url":      _str(cols[10]),
        "payload":        Jsonb(payload),
    }

_MAPPERS: dict[str, tuple] = {
    "abtranny": (_map_abtranny, 12),
}

# --- DB seed ---

def _seed_db(conn) -> tuple[int, dict[str, int]]:
    """Upsert provider/sites/feeds. Returns (provider_id, {site_slug: feed_id})."""
    provider_id = conn.execute(
        """
        INSERT INTO providers (slug, name) VALUES (%s, %s)
        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (PROVIDER_SLUG, PROVIDER_NAME),
    ).fetchone()[0]

    site_ids: dict[str, int] = {}
    for s in _SITES:
        site_ids[s["slug"]] = conn.execute(
            """
            INSERT INTO sites (provider_id, slug, domain) VALUES (%s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET domain = EXCLUDED.domain
            RETURNING id
            """,
            (provider_id, s["slug"], s["domain"]),
        ).fetchone()[0]

    feed_ids: dict[str, int] = {}
    for f in _FEEDS:
        feed_ids[f["site_slug"]] = conn.execute(
            """
            INSERT INTO feeds
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
INSERT INTO raw_videos (
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
    parser = argparse.ArgumentParser(description="Ingest adultnext.com feeds")
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
