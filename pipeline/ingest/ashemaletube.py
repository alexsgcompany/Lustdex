"""Ingest pipeline for ashemaletube.com manual export files.

The admin endpoint at adminex.ashemaletube.com is behind Cloudflare Bot
Management with a JS challenge, so this script reads a locally downloaded
export file. The original URL (with <JWT> placeholder) is stored in
raw.feeds.feed_url for documentation only.
"""

import argparse
import gzip
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger(__name__)

PROVIDER_SLUG = "ashemaletube"
PROVIDER_NAME = "ashemaletube.com"

_SITES = [
    {"slug": "ashemaletube-com", "domain": "ashemaletube.com"},
]

_FEEDS = [
    {
        "site_slug": "ashemaletube-com",
        "feed_url": (
            "https://adminex.ashemaletube.com/rss-final/"
            "?SubmitCheck=<JWT>&h=<hash>"
            "&val1=11&val2=1&val3=2&val4=8&val5=6&val6=5&val7=0"
            "&routerDomain=router&number=100&size=full&niche=0&quality=1&submit=Send"
        ),
        "feed_format": "csv",
        "has_header": False,
        "max_limit": 999_999_999,
        "niche": "trans",
        "sub_niche": "shemale",
    },
]

EXPECTED_COLS = 18

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
            return datetime.strptime(v, "%Y-%m-%d %H:%M UTC")
        except ValueError:
            return None
    return None

def _dur(val: str) -> int | None:
    """Parse 'mm:ss' or 'h:mm:ss' into seconds."""
    v = _str(val)
    if not v:
        return None
    try:
        parts = [int(p) for p in v.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None

# --- row mapper ---

def _map_row(cols: list[str]) -> dict:
    payload: dict = {}
    if e := _str(cols[4]):       payload["embed"] = e
    if v := _int(cols[9]):       payload["views"] = v
    if previews := _split(cols[11]): payload["preview_thumbs"] = previews
    if q := _str(cols[13]):      payload["quality"] = q
    if studio := _str(cols[16]): payload["studio"] = studio
    return {
        "external_id":    cols[12].strip(),
        "target_url":     _str(cols[3]),
        "title":          _str(cols[1]),
        "description":    None,
        "tags_raw":       _split(cols[5]) + _split(cols[6]),
        "performers_raw": _split(cols[7]),
        "duration_sec":   _dur(cols[8]),
        "published_at":   _dt(cols[15]),
        "thumb_url":      _str(cols[10]),
        "payload":        Jsonb(payload),
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

BATCH_SIZE = 5000

def _open_text(path: Path):
    """Open .txt or .gz transparently as text."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")

def _parse_file_and_upsert(
    conn, path: Path, provider_id: int, feed_id: int
) -> tuple[int, int]:
    total, errors, batch = 0, 0, []
    with _open_text(path) as fh, conn.cursor() as cur:
        for i, line in enumerate(fh, start=1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                cols = line.split("|")
                if len(cols) != EXPECTED_COLS:
                    raise ValueError(f"expected {EXPECTED_COLS} cols, got {len(cols)}")
                row = _map_row(cols)
                row["provider_id"] = provider_id
                row["feed_id"] = feed_id
                batch.append(row)
            except Exception as e:
                log.warning("row %d: %s", i, e)
                errors += 1

            if len(batch) >= BATCH_SIZE:
                cur.executemany(_UPSERT_SQL, batch)
                conn.commit()
                total += len(batch)
                log.info("  upserted %d rows (total %d)", len(batch), total)
                batch = []

        if batch:
            cur.executemany(_UPSERT_SQL, batch)
            conn.commit()
            total += len(batch)
            log.info("  upserted %d rows (total %d)", len(batch), total)

    return total, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest ashemaletube.com manual export")
    parser.add_argument("--file", type=Path, required=True, help="path to |-separated export")
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"file not found: {args.file}")

    feed_cfg = _FEEDS[0]
    with get_conn() as conn:
        provider_id, feed_ids = _seed_db(conn)
        feed_id = feed_ids[feed_cfg["site_slug"]]
        log.info("ingest %s  file=%s", feed_cfg["site_slug"], args.file)
        n, err = _parse_file_and_upsert(conn, args.file, provider_id, feed_id)
        log.info("done   %s  +%d rows, %d errors", feed_cfg["site_slug"], n, err)


if __name__ == "__main__":
    main()
