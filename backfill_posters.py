"""Backfill real movie posters onto thumbnails, most-popular-first.

Most of the IMDb-imported catalog has no poster, so the grids fall back to a
generated SVG title-card. This script fetches real posters from OMDb (keyed by
the stored IMDb id) for the highest-voted movies first -- which is exactly what
shows at the top of every genre row -- and writes them to the `poster` column.
The site's _card.html already renders `m.poster` when present, so filled rows
get real images automatically with no template change.

OMDb's free tier allows 1,000 requests/day, so the script stops at a daily cap
(default 950, leaving headroom for the live site's lazy enrichment) and you
re-run it the next day to continue down the popularity list.

Targets the same database the app uses:
  * DATABASE_URL set  -> Postgres (your live Render data)   <-- recommended
  * DATABASE_URL unset -> local screamstream.db (then re-run migrate_to_postgres.py)

Usage:
  # PowerShell, against the live Postgres (posters show up immediately):
  cd C:\\Users\\jackk\\movie-site
  $env:DATABASE_URL = "postgres://USER:PASS@HOST/DB"   # the External URL
  python backfill_posters.py                 # up to 950 posters
  python backfill_posters.py --limit 200      # smaller run
  python backfill_posters.py --delay 0.1      # gentler on OMDb

Requires psycopg2-binary only when using Postgres (already in requirements.txt).
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "screamstream.db")
MISS_FILE = os.path.join(BASE_DIR, ".poster_misses.txt")  # imdb ids with no poster
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)


def _env(key):
    """A value from the environment, falling back to the local .env file."""
    val = os.environ.get(key)
    if val:
        return val
    path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def connect():
    """Open the same database the app uses; returns (conn, placeholder)."""
    if USE_PG:
        import psycopg2
        url = DATABASE_URL
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        conn = psycopg2.connect(url)
        return conn, "%s"
    conn = sqlite3.connect(SQLITE_PATH)
    return conn, "?"


def load_misses():
    if not os.path.exists(MISS_FILE):
        return set()
    with open(MISS_FILE, encoding="utf-8") as fh:
        return {ln.strip() for ln in fh if ln.strip()}


def omdb_poster(imdb_tt, api_key):
    """Return (poster_url, limit_reached). poster_url is '' if none/N/A."""
    url = (f"https://www.omdbapi.com/?i={urllib.parse.quote(imdb_tt)}"
           f"&apikey={api_key}")
    try:
        with urlopen(Request(url, headers={"User-Agent": "ScreamStream"}),
                     timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:                       # network / decode hiccup
        print(f"    ! fetch error: {exc}")
        return "", False
    if data.get("Response") == "False":
        if "limit" in (data.get("Error") or "").lower():
            return "", True                        # daily quota exhausted
        return "", False
    poster = data.get("Poster") or ""
    return ("" if poster == "N/A" else poster), False


def main():
    ap = argparse.ArgumentParser(description="Backfill movie posters from OMDb.")
    ap.add_argument("--limit", type=int, default=950,
                    help="max OMDb lookups this run (default 950, cap is 1000/day)")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds to pause between OMDb calls (default 0)")
    args = ap.parse_args()

    api_key = _env("OMDB_API_KEY")
    if not api_key:
        sys.exit("OMDB_API_KEY not set (env or .env).")
    if USE_PG:
        print("Target: Postgres (DATABASE_URL) — posters appear on the live site.")
    else:
        print(f"Target: local SQLite {SQLITE_PATH}\n"
              "  (run migrate_to_postgres.py afterwards to push these to the live DB)")

    conn, ph = connect()
    cur = conn.cursor()

    # Candidates: no poster yet, but have an IMDb id to look up; most-voted first.
    where = ("(poster IS NULL OR poster = '') "
             "AND imdb_tt IS NOT NULL AND imdb_tt <> ''")
    cur.execute(f"SELECT COUNT(*) FROM movies WHERE {where}")
    remaining = cur.fetchone()[0]
    print(f"Movies still needing a poster: {remaining}")
    if not remaining:
        print("Nothing to do — every eligible movie already has a poster.")
        return

    # Pull a generous candidate window (some are known misses we'll skip locally).
    misses = load_misses()
    window = args.limit + len(misses) + 500
    cur.execute(
        f"SELECT id, title, imdb_tt FROM movies WHERE {where} "
        f"ORDER BY COALESCE(imdb_votes, 0) DESC LIMIT {ph}",
        (window,),
    )
    candidates = cur.fetchall()

    found = checked = 0
    new_misses = []
    miss_fh = open(MISS_FILE, "a", encoding="utf-8")
    try:
        for mid, title, imdb_tt in candidates:
            if checked >= args.limit:
                break
            if imdb_tt in misses:
                continue
            checked += 1
            poster, limited = omdb_poster(imdb_tt, api_key)
            if limited:
                print("  ! OMDb daily limit reached — stopping, resume tomorrow.")
                checked -= 1
                break
            if poster:
                cur.execute(f"UPDATE movies SET poster = {ph} WHERE id = {ph}",
                            (poster, mid))
                found += 1
                print(f"  {found:>4}  {(title or '')[:45]:45}  OK")
                if found % 25 == 0:
                    conn.commit()
            else:
                new_misses.append(imdb_tt)
                miss_fh.write(imdb_tt + "\n")
        conn.commit()
    finally:
        miss_fh.close()
        cur.close()
        conn.close()

    print(f"\nDone. Looked up {checked}, added {found} posters, "
          f"{len(new_misses)} had no poster on OMDb.")
    print(f"~{remaining - found} eligible movies still need one - "
          "re-run tomorrow to continue down the popularity list.")


if __name__ == "__main__":
    main()
