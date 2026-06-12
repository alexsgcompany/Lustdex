import os

import psycopg

import pipeline.common.config  # noqa: F401 — ensures .env is loaded


def get_conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])
