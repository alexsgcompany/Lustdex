"""LLM batch: classify performer gender from name + video context (spec 11 §4c).

Run:  python -m pipeline.performers.classify_gender [--limit N] [--model M]

Targets active performers with gender_source IS NULL (resumable: only fills
gaps). For each, builds context (sample titles + top tags) from the junctions,
asks the LLM for {is_person, gender, confidence}, and writes the result with
gender_source='llm'. is_person=false → status='hidden' (noise the floor missed).
Admin override (gender_source='admin') always wins and is never overwritten here.
"""

import argparse
from pathlib import Path

import pipeline.common.config  # noqa: F401  (loads .env)
from pipeline.common.db import get_conn
from pipeline.common.log import get_logger
from pipeline.common.runs import pipeline_run
from pipeline.llm.client import DEFAULT_MODEL, chat_json

log = get_logger(__name__)
JOB = "performers.classify_gender"
VALID = {"female", "male", "trans", "unknown"}
N_TITLES = 8
N_TAGS = 12

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "performer_gender.md"
PROMPT = _PROMPT_PATH.read_text()

_CONTEXT_SQL = """
WITH vp AS (
    SELECT video_id FROM cat.video_performers WHERE performer_id = %(pid)s
)
SELECT
    (SELECT array_agg(t) FROM (
        SELECT v.title AS t FROM cat.videos v JOIN vp ON vp.video_id = v.id
        WHERE v.title IS NOT NULL ORDER BY v.published_at DESC NULLS LAST
        LIMIT %(n_titles)s) s),
    (SELECT array_agg(name) FROM (
        SELECT tg.name FROM cat.video_tags vt
        JOIN vp ON vp.video_id = vt.video_id
        JOIN cat.tags tg ON tg.id = vt.tag_id
        GROUP BY tg.name ORDER BY count(*) DESC
        LIMIT %(n_tags)s) s)
"""


def _build_prompt(name: str, titles: list[str], tags: list[str]) -> str:
    return (
        PROMPT
        .replace("{name}", name)
        .replace("{titles}", "\n".join(f"- {t}" for t in titles) or "(none)")
        .replace("{tags}", ", ".join(tags) or "(none)")
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    with pipeline_run(JOB) as stats:
        with get_conn() as conn:
            _run(conn, stats, args.limit, args.model)


def _run(conn, stats: dict, limit: int | None, model: str) -> None:
    sql = (
        "SELECT id, name FROM cat.performers"
        " WHERE status = 'active' AND gender_source IS NULL"
        " ORDER BY n_videos DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    targets = conn.execute(sql).fetchall()

    done = hidden = failed = 0
    for pid, name in targets:
        titles, tags = conn.execute(
            _CONTEXT_SQL, {"pid": pid, "n_titles": N_TITLES, "n_tags": N_TAGS}
        ).fetchone()
        prompt = _build_prompt(name, titles or [], tags or [])
        try:
            out = chat_json(prompt, model=model)
        except Exception as e:  # noqa: BLE001 — one bad call must not kill the run (rule 11)
            failed += 1
            log.warning("LLM failed for id=%d %r: %s", pid, name, e)
            continue

        gender = out.get("gender", "unknown")
        if gender not in VALID:
            gender = "unknown"
        conf = out.get("confidence")
        if out.get("is_person") is False:
            conn.execute(
                "UPDATE cat.performers SET status='hidden', gender=%s,"
                " gender_source='llm', gender_confidence=%s, updated_at=now()"
                " WHERE id=%s",
                ("unknown", conf, pid),
            )
            hidden += 1
        else:
            conn.execute(
                "UPDATE cat.performers SET gender=%s, gender_source='llm',"
                " gender_confidence=%s, updated_at=now() WHERE id=%s",
                (gender, conf, pid),
            )
            done += 1
        conn.commit()  # per-row: resumable

    stats.update(targets=len(targets), classified=done, hidden=hidden, failed=failed)
    log.info(
        "targets=%d  classified=%d  hidden=%d  failed=%d",
        len(targets), done, hidden, failed,
    )


if __name__ == "__main__":
    main()
