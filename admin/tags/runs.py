import subprocess
import sys

import streamlit as st
from pipeline.common.db import get_conn
from tags import queries

# (label, python module, job name stored in pipeline_runs)
_JOBS = [
    ("Collect", "pipeline.tags.collect", "tags.collect"),
    ("Cascade", "pipeline.tags.cascade", "tags.cascade"),
    ("Apply",   "pipeline.tags.apply",   "tags.apply"),
]

_STATUS_ICON = {"done": "✅", "running": "⏳", "failed": "❌"}


def render() -> None:
    st.header("Pipeline Runs")

    with get_conn() as conn:
        running = {r[0] for r in conn.execute(queries.RUNNING_JOBS).fetchall()}

    cols = st.columns(len(_JOBS) + 1)
    for i, (label, module, job_name) in enumerate(_JOBS):
        is_running = job_name in running
        if cols[i].button(label, disabled=is_running, key=f"run_{job_name}"):
            subprocess.Popen([sys.executable, "-m", module])
            st.info(f"Started **{label}**. Refresh to see status.")
    if cols[-1].button("Refresh"):
        st.rerun()

    st.divider()

    with get_conn() as conn:
        run_rows = conn.execute(queries.PIPELINE_RUNS).fetchall()

    for run in run_rows:
        run_id, job, status, started_at, finished_at, duration_sec, stats, error = run
        icon = _STATUS_ICON.get(status, "❓")
        dur  = f"{duration_sec}s" if duration_sec is not None else "—"
        ts   = started_at.strftime("%Y-%m-%d %H:%M:%S") if started_at else "—"

        stats_str = ""
        if stats and isinstance(stats, dict):
            stats_str = "  ·  " + "  ·  ".join(f"{k}: {v}" for k, v in stats.items())

        line = f"{icon} **{job}**  {ts}  {dur}{stats_str}"

        if error:
            with st.expander(line):
                st.code(error)
        else:
            st.write(line)
