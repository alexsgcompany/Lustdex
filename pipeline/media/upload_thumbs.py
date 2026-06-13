"""Download provider thumbnails, convert to WebP, upload to Bunny.net storage.

Idempotent: re-runs skip videos already in `video_assets`.
Errors per video are logged + counted; the run never aborts mid-batch.

Usage:
    python -m pipeline.media.upload_thumbs
    python -m pipeline.media.upload_thumbs --limit 500
    python -m pipeline.media.upload_thumbs --workers 16
"""

import argparse
import hashlib
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import httpx
from PIL import Image

from pipeline.common.db import get_conn
from pipeline.common.log import get_logger

log = get_logger("media.upload_thumbs")

KIND = "thumb"
RESIZE_WIDTH = 640
WEBP_QUALITY = 80
WEBP_METHOD = 6
DOWNLOAD_TIMEOUT = 10.0
UPLOAD_TIMEOUT = 30.0
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


@dataclass(slots=True)
class Job:
    video_id: int
    provider_id: int
    slug: str
    thumb_url: str


@dataclass(slots=True)
class Result:
    video_id: int
    source_url: str
    path: str
    sha256: str
    width: int
    height: int
    bytes_: int


def _fetch_pending(conn, limit: int | None) -> list[Job]:
    sql = """
        SELECT v.id, v.provider_id, v.slug, rv.thumb_url
        FROM cat.videos v
        JOIN raw.raw_videos rv ON rv.id = v.id
        LEFT JOIN cat.video_assets va
               ON va.video_id = v.id AND va.kind = %s
        WHERE va.id IS NULL
          AND rv.thumb_url IS NOT NULL
          AND rv.thumb_url <> ''
        ORDER BY v.id
    """
    params: list = [KIND]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [
        Job(video_id=r[0], provider_id=r[1], slug=r[2], thumb_url=r[3])
        for r in rows
    ]


def _download(client: httpx.Client, url: str) -> bytes:
    with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as resp:
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_bytes():
            buf.extend(chunk)
            if len(buf) > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"image too large (> {MAX_DOWNLOAD_BYTES} bytes)")
        return bytes(buf)


def _to_webp(raw: bytes) -> tuple[bytes, int, int]:
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.width > RESIZE_WIDTH:
        new_h = round(img.height * RESIZE_WIDTH / img.width)
        img = img.resize((RESIZE_WIDTH, new_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
    return out.getvalue(), img.width, img.height


def _upload(client: httpx.Client, zone: str, host: str, key: str, path: str, body: bytes) -> None:
    url = f"https://{host}/{zone}/{path}"
    resp = client.put(
        url,
        content=body,
        headers={"AccessKey": key, "Content-Type": "image/webp"},
        timeout=UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()


def _process(job: Job, client: httpx.Client, zone: str, host: str, key: str) -> Result:
    raw = _download(client, job.thumb_url)
    sha = hashlib.sha256(raw).hexdigest()
    webp_bytes, w, h = _to_webp(raw)
    shard = f"{job.video_id // 10000:03d}"
    path = f"i/{job.provider_id}/{shard}/{job.slug}-{job.video_id}.webp"
    _upload(client, zone, host, key, path, webp_bytes)
    return Result(
        video_id=job.video_id,
        source_url=job.thumb_url,
        path=path,
        sha256=sha,
        width=w,
        height=h,
        bytes_=len(webp_bytes),
    )


def _insert(conn, r: Result) -> None:
    conn.execute(
        """
        INSERT INTO cat.video_assets
            (video_id, kind, path, sha256, width, height, bytes, source_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (video_id, kind) DO NOTHING
        """,
        (r.video_id, KIND, r.path, r.sha256, r.width, r.height, r.bytes_, r.source_url),
    )
    conn.execute(
        "UPDATE cat.videos SET has_thumb = true WHERE id = %s AND has_thumb = false",
        (r.video_id,),
    )


def run(limit: int | None, workers: int) -> int:
    zone = os.environ["BUNNY_STORAGE_ZONE"]
    key = os.environ["BUNNY_STORAGE_KEY"]
    host = os.environ.get("BUNNY_STORAGE_HOST", "storage.bunnycdn.com")
    if not key:
        log.error("BUNNY_STORAGE_KEY is empty — set it in .env")
        return 2

    with get_conn() as conn:
        jobs = _fetch_pending(conn, limit)
    log.info("pending: %d videos (workers=%d)", len(jobs), workers)
    if not jobs:
        return 0

    ok = 0
    failed = 0
    with (
        httpx.Client() as client,
        get_conn() as conn,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futures = {pool.submit(_process, j, client, zone, host, key): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                failed += 1
                log.error("video_id=%d url=%s failed: %s", job.video_id, job.thumb_url, exc)
                continue
            _insert(conn, result)
            ok += 1
            if ok % 50 == 0:
                conn.commit()
                log.info("progress: ok=%d failed=%d", ok, failed)
        conn.commit()

    log.info("done: ok=%d failed=%d total=%d", ok, failed, len(jobs))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="max videos to process")
    parser.add_argument("--workers", type=int, default=8, help="parallel workers")
    args = parser.parse_args()
    sys.exit(run(args.limit, args.workers))


if __name__ == "__main__":
    main()
