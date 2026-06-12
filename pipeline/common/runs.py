"""Context manager that bookends a pipeline job with a pipeline_runs row."""

import traceback
from contextlib import contextmanager
from typing import Generator

from psycopg.types.json import Jsonb

import pipeline.common.config  # noqa: F401
from pipeline.common.db import get_conn


@contextmanager
def pipeline_run(job: str) -> Generator[dict, None, None]:
    with get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (job, status) VALUES (%s, 'running') RETURNING id",
            (job,),
        ).fetchone()[0]
        conn.commit()

    stats: dict = {}
    try:
        yield stats
        with get_conn() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status='done', finished_at=now(), stats=%s WHERE id=%s",
                (Jsonb(stats), run_id),
            )
            conn.commit()
    except Exception:
        with get_conn() as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status='failed', finished_at=now(), error=%s WHERE id=%s",
                (traceback.format_exc()[:2000], run_id),
            )
            conn.commit()
        raise
