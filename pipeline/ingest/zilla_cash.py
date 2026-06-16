"""Ingest pipeline for zilla.cash feeds (around-xxx, analdin-com, xozilla-com, xtits-com).

Pragmatic note on the provider model: zilla.cash is conceptually one provider
that owns several sites with independent external_id namespaces, but
cat.videos.UNIQUE(provider_id, external_id) doesn't tolerate that — so each
zilla.cash site currently lives as its own cat.providers row (slug = site
slug, name = domain). A proper cat.sites + cat.videos.site_id refactor is in
TODO Post-MVP; until then `provider` here means `feed source`, not network.
"""

import argparse
from datetime import datetime

import httpx
from psycopg.types.json import Jsonb

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)

_SITES = [
    {"slug": "around-xxx",  "domain": "around.xxx",  "provider_name": "around.xxx"},
    {"slug": "analdin-com", "domain": "analdin.com", "provider_name": "analdin.com"},
    {"slug": "xozilla-com", "domain": "xozilla.com", "provider_name": "xozilla.com"},
    {"slug": "xtits-com",   "domain": "xtits.com",   "provider_name": "xtits.com"},
]

# Shared CSV column spec for /admin/feeds/default/ endpoints (analdin-style).
_ANALDIN_STYLE_COLS = (
    "id%7Clink%7Ctitle%7Cdescription%7Ccategories"
    "%7Cmodels%7Cduration%7Cpost_date%7Cmain_screenshot%7Cembed%7Ccustom1"
)

_FEEDS = [
    {
        "site_slug": "around-xxx",
        "feed_url": "https://zilla.cash/around/feed/?limit={limit}",
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "mix",
        "sub_niche": "mix",
        "supports_category": False,
    },
    {
        "site_slug": "analdin-com",
        "feed_url": (
            "https://www.analdin.com/admin/feeds/default/"
            "?feed_format=csv&limit={limit}&screenshot_format=source"
            "&csv_separator=%7C"
            f"&csv_columns={_ANALDIN_STYLE_COLS}"
        ),
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "mix",
        "sub_niche": "mix",
        "supports_category": True,  # accepts &category=<slug>&skip=<n>
    },
    # mix-sites tapped only for the shemale slice: category_only guards them
    # from being dumped wholesale by a no-arg run.
    {
        "site_slug": "xozilla-com",
        "feed_url": (
            "https://www.xozilla.com/admin/feeds/default/"
            "?feed_format=csv&limit={limit}"
            "&csv_separator=%7C"
            f"&csv_columns={_ANALDIN_STYLE_COLS}"
        ),
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "trans",
        "sub_niche": "trans",
        "supports_category": True,
        "category_only": True,
    },
    {
        "site_slug": "xtits-com",
        "feed_url": (
            "https://www.xtits.com/admin/feeds/default/"
            "?feed_format=csv&limit={limit}"
            "&csv_separator=%7C"
            f"&csv_columns={_ANALDIN_STYLE_COLS}"
        ),
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "trans",
        "sub_niche": "trans",
        "supports_category": True,
        "category_only": True,
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
    "xozilla-com": (_map_analdin, 11),
    "xtits-com": (_map_analdin, 11),
}

# --- DB seed ---

def _seed_db(conn) -> tuple[dict[str, int], dict[str, int]]:
    """Upsert per-site provider + site + feed rows.

    Returns ({site_slug: provider_id}, {site_slug: feed_id}).

    Also backfills raw.raw_videos.provider_id and cat.videos.provider_id for
    rows ingested before the per-site-provider split — old rows pointed at the
    single 'zilla-cash' provider row, which is no longer used.
    """
    site_providers: dict[str, int] = {}
    site_ids: dict[str, int] = {}
    for s in _SITES:
        site_providers[s["slug"]] = conn.execute(
            """
            INSERT INTO cat.providers (slug, name) VALUES (%s, %s)
            ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (s["slug"], s["provider_name"]),
        ).fetchone()[0]
        site_ids[s["slug"]] = conn.execute(
            """
            INSERT INTO raw.sites (provider_id, slug, domain) VALUES (%s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                provider_id = EXCLUDED.provider_id,
                domain      = EXCLUDED.domain
            RETURNING id
            """,
            (site_providers[s["slug"]], s["slug"], s["domain"]),
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

    # Backfill: relink old rows from the legacy single-provider model.
    # Idempotent — touches only rows where provider_id is still wrong.
    backfilled_raw = conn.execute(
        """
        UPDATE raw.raw_videos rv
        SET    provider_id = s.provider_id
        FROM   raw.feeds f, raw.sites s
        WHERE  rv.feed_id = f.id
          AND  f.site_id  = s.id
          AND  rv.provider_id <> s.provider_id
        """
    ).rowcount
    backfilled_cat = conn.execute(
        """
        UPDATE cat.videos cv
        SET    provider_id = rv.provider_id
        FROM   raw.raw_videos rv
        WHERE  cv.id = rv.id
          AND  cv.provider_id <> rv.provider_id
        """
    ).rowcount
    if backfilled_raw or backfilled_cat:
        log.info("backfill: raw.raw_videos=%d, cat.videos=%d", backfilled_raw, backfilled_cat)

    conn.commit()
    return site_providers, feed_ids

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

def _fetch_and_upsert(
    conn, url: str, feed_cfg: dict, provider_id: int, feed_id: int
) -> tuple[int, int]:
    """Fetch one URL, parse CSV, upsert. Returns (parsed_rows, errors)."""
    mapper, expected_cols = _MAPPERS[feed_cfg["site_slug"]]

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
    return len(rows), errors


def _ingest_feed(
    conn, feed_cfg: dict, provider_id: int, feed_id: int,
    limit: int, category: str | None = None,
) -> None:
    site = feed_cfg["site_slug"]

    if category is None:
        if feed_cfg.get("category_only"):
            log.info("skip   %s  — category_only, requires --category", site)
            return
        url = feed_cfg["feed_url"].format(limit=limit)
        log.info("fetch  %s  limit=%d", site, limit)
        n, err = _fetch_and_upsert(conn, url, feed_cfg, provider_id, feed_id)
        log.info("done   %s  +%d rows, %d errors", site, n, err)
        return

    if not feed_cfg.get("supports_category"):
        log.info("skip   %s  — no category filter support", site)
        return

    # Paginate via &skip until empty. Server caps each window at ~999.
    skip, total, total_err = 0, 0, 0
    while True:
        url = feed_cfg["feed_url"].format(limit=limit) + f"&category={category}&skip={skip}"
        log.info("fetch  %s  category=%s  skip=%d", site, category, skip)
        n, err = _fetch_and_upsert(conn, url, feed_cfg, provider_id, feed_id)
        total += n
        total_err += err
        if n == 0:
            break
        skip += n
    log.info("done   %s  category=%s  +%d rows, %d errors", site, category, total, total_err)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest zilla.cash feeds")
    parser.add_argument("--limit", type=int, default=None, help="override max_limit")
    parser.add_argument(
        "--category", type=str, default=None,
        help="filter to a single category (paginates via skip; analdin-com only)",
    )
    args = parser.parse_args()

    with get_conn() as conn:
        site_providers, feed_ids = _seed_db(conn)
        for feed_cfg in _FEEDS:
            site_slug = feed_cfg["site_slug"]
            limit = args.limit or feed_cfg["max_limit"]
            _ingest_feed(
                conn, feed_cfg, site_providers[site_slug], feed_ids[site_slug],
                limit, args.category,
            )


if __name__ == "__main__":
    main()
