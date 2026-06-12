"""Apply pending SQL migrations from db/migrations/ in order."""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run() -> None:
    db_url = os.environ["DATABASE_URL"]
    with psycopg.connect(db_url) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        conn.commit()

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in applied:
                print(f"skip  {version}")
                continue
            print(f"apply {version} ...", end=" ", flush=True)
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            print("ok")


if __name__ == "__main__":
    run()
