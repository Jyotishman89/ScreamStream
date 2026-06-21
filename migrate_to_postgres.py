import os
import sqlite3
import sys

import psycopg2
import psycopg2.extras

from app import PG_SCHEMA

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "screamstream.db")
TABLES = ["users", "movies", "history"]
BATCH = 5000

def _load_env_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None

def _require_ssl(url):
    if "sslmode=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslmode=require"

def main():
    url = _load_env_database_url()
    if not url:
        sys.exit(
            "DATABASE_URL is not set.\n"
            "Set it to your cloud Postgres connection URL (e.g. Neon) and re-run, e.g.\n"
            '  $env:DATABASE_URL = "postgres://user:pass@host/db"   (PowerShell)'
        )
    if not os.path.exists(SQLITE_PATH):
        sys.exit(f"Local catalog not found: {SQLITE_PATH}")

    sl = sqlite3.connect(SQLITE_PATH)
    sl.row_factory = sqlite3.Row
    pg = psycopg2.connect(_require_ssl(url))
    pg.autocommit = False
    cur = pg.cursor()

    print("Creating schema in Postgres (if absent)...")
    cur.execute(PG_SCHEMA)
    pg.commit()

    for table in TABLES:
        cols = [r[1] for r in sl.execute(f"PRAGMA table_info({table})")]
        if not cols:
            print(f"  {table}: not present locally, skipping")
            continue
        quoted = ", ".join(f'"{c}"' for c in cols)
        insert = (
            f'INSERT INTO {table} ({quoted}) VALUES %s ON CONFLICT DO NOTHING'
        )

        total = sl.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: copying {total} rows...")
        rows, done = sl.execute(f"SELECT {quoted} FROM {table}"), 0
        while True:
            chunk = rows.fetchmany(BATCH)
            if not chunk:
                break
            psycopg2.extras.execute_values(
                cur, insert, [tuple(r) for r in chunk], page_size=BATCH
            )
            done += len(chunk)
            print(f"    {done}/{total}", end="\r")
        pg.commit()
        print(f"    {done}/{total}  done")

    cur.execute(
        "SELECT setval(pg_get_serial_sequence('users','id'), "
        "COALESCE((SELECT MAX(id) FROM users), 1), true)"
    )
    pg.commit()

    cur.close()
    pg.close()
    sl.close()
    print("Migration complete.")

if __name__ == "__main__":
    main()
