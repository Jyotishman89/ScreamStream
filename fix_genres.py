import os


def _load_database_url():
    if os.environ.get("DATABASE_URL"):
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    os.environ["DATABASE_URL"] = value
                return


_load_database_url()
os.environ.setdefault("SECRET_KEY", "fix-genres-maintenance-script")

from app import app, get_db, _row_get, USE_PG
from genre_overrides import GENRE_OVERRIDES


def main():
    print("Target:", "PostgreSQL (Neon)" if USE_PG else "local SQLite")
    with app.app_context():
        db = get_db()
        changed = missing = same = 0
        for tt, title, genre in GENRE_OVERRIDES:
            row = db.execute(
                "SELECT genre FROM movies WHERE imdb_tt = ?", (tt,)
            ).fetchone()
            if row is None:
                missing += 1
                print(f"  missing   {tt}  {title}")
                continue
            current = _row_get(row, "genre")
            if current == genre:
                same += 1
                continue
            db.execute(
                "UPDATE movies SET genre = ? WHERE imdb_tt = ?", (genre, tt)
            )
            changed += 1
            print(f"  {current:>16} -> {genre:<16} {title}")
        db.commit()
        print(
            f"\nUpdated {changed}, already-correct {same}, "
            f"not-in-catalog {missing} (of {len(GENRE_OVERRIDES)})."
        )


if __name__ == "__main__":
    main()
