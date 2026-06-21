
import gzip
import os
import sys
import urllib.request

from app import app, get_db, init_db, seed_movies

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, ".imdb_cache")
BASICS_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"

IMDB_GENRE_MAP = {
    "Action": "Action", "Adventure": "Adventure", "Animation": "Animation",
    "Biography": "Drama", "Comedy": "Comedy", "Crime": "Crime",
    "Documentary": "Documentary", "Drama": "Drama", "Family": "Family",
    "Fantasy": "Fantasy", "Film-Noir": "Crime", "History": "History",
    "Horror": "Horror", "Music": "Music", "Musical": "Music",
    "Mystery": "Mystery", "News": "Documentary", "Romance": "Romance",
    "Sci-Fi": "Science Fiction", "Sport": "Drama", "Thriller": "Thriller",
    "War": "War", "Western": "Western",
}
CATEGORY_PRIORITY = [
    "Horror", "Science Fiction", "Fantasy", "Animation", "Western", "War",
    "Crime", "Mystery", "Thriller", "Adventure", "Action", "Music",
    "Documentary", "Romance", "Comedy", "Family", "History", "Drama",
]
PRIORITY_INDEX = {c: i for i, c in enumerate(CATEGORY_PRIORITY)}

def pick_category(imdb_genres):
    cats = {IMDB_GENRE_MAP[g] for g in imdb_genres if g in IMDB_GENRE_MAP}
    if not cats:
        return None
    return min(cats, key=lambda c: PRIORITY_INDEX.get(c, 999))

def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  cached  {os.path.basename(dest)}")
        return
    print(f"  downloading {os.path.basename(dest)} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "ScreamStream/1.0"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req) as resp, open(tmp, "wb") as out:
        got = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            print(f"\r    {got / 1e6:7.1f} MB", end="", flush=True)
    os.replace(tmp, dest)
    print()

def load_ratings(path):
    ratings = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            tconst, avg, votes = line.rstrip("\n").split("\t")
            try:
                ratings[tconst] = (float(avg), int(votes))
            except ValueError:
                pass
    return ratings

def flush(db, batch):
    db.executemany(
        """INSERT OR IGNORE INTO movies
           (id, title, genre, year, mpaa, imdb, rotten, runtime, description,
            poster, trailer, video, platforms, enriched, imdb_tt, imdb_votes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        batch,
    )

def main():
    try:
        min_votes = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    except ValueError:
        min_votes = 1000

    os.makedirs(CACHE, exist_ok=True)
    basics = os.path.join(CACHE, "title.basics.tsv.gz")
    ratings_file = os.path.join(CACHE, "title.ratings.tsv.gz")

    print("Fetching IMDb datasets:")
    download(RATINGS_URL, ratings_file)
    download(BASICS_URL, basics)

    print("Loading ratings ...", flush=True)
    ratings = load_ratings(ratings_file)
    print(f"  {len(ratings):,} rated titles")

    with app.app_context():
        init_db()
        seed_movies()
        db = get_db()
        existing = {r[0] for r in db.execute("SELECT id FROM movies")}

        scanned = inserted = 0
        batch = []
        print(f"Scanning movies (min {min_votes} votes) ...", flush=True)
        with gzip.open(basics, "rt", encoding="utf-8") as f:
            next(f)
            for line in f:
                p = line.rstrip("\n").split("\t")
                tconst, ttype, title, _o, adult, start, _e, runtime, genres = p
                if ttype != "movie" or adult != "0":
                    continue
                scanned += 1
                rating = ratings.get(tconst)
                votes = rating[1] if rating else 0
                if votes < min_votes or tconst in existing:
                    continue
                genre = pick_category(genres.split(",")) if genres != "\\N" else None
                if not genre:
                    continue
                year = int(start) if start.isdigit() else None
                rt = int(runtime) if runtime.isdigit() else None
                imdb = rating[0] if rating else None
                batch.append((tconst, title, genre, year, "", imdb, None, rt,
                              "", "", "", "", "", 0, tconst, votes))
                existing.add(tconst)
                inserted += 1
                if len(batch) >= 2000:
                    flush(db, batch)
                    batch.clear()
                    print(f"\r  scanned {scanned:,}  inserted {inserted:,}",
                          end="", flush=True)
        if batch:
            flush(db, batch)
        db.commit()
    print(f"\nDone. Inserted {inserted:,} movies across the categories.")
    print("Start the site:  python app.py")

if __name__ == "__main__":
    main()
