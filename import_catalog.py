
import sys

from app import (
    TMDB_API_KEY, TMDB_DISCOVER, app, init_db, seed_movies, tmdb_bulk_import,
)

def main():
    try:
        pages = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    except ValueError:
        print(f"Invalid page count: {sys.argv[1]!r}. Using 10.")
        pages = 10
    pages = max(1, min(pages, 500))

    if not TMDB_API_KEY:
        print("TMDB_API_KEY is not set. Add it to movie-site/.env, e.g.:")
        print("    TMDB_API_KEY=your_key_here")
        print("Get a free key at https://www.themoviedb.org/settings/api")
        sys.exit(1)

    genres = [g for g in TMDB_DISCOVER if g != "Anime"] + ["Anime"]

    print(f"Importing up to ~{pages * 20} titles per category "
          f"across {len(genres)} categories...\n")
    with app.app_context():
        init_db()
        seed_movies()
        total = 0
        for genre in genres:
            print(f"  {genre:<18} ", end="", flush=True)
            added = tmdb_bulk_import(genre, pages)
            total += added
            print(f"+{added}")
    print(f"\nDone. {total} new title(s) added. Start the site: python app.py")

if __name__ == "__main__":
    main()
