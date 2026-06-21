import argparse
import getpass
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, "screamstream.db")
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)


def connect():
    if USE_PG:
        import psycopg2
        url = DATABASE_URL
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        return psycopg2.connect(url), "%s"
    return sqlite3.connect(SQLITE_PATH), "?"


def main():
    ap = argparse.ArgumentParser(
        description="Reset a ScreamStream account password. Targets the Neon/Postgres "
                    "database when DATABASE_URL is set, otherwise the local SQLite file."
    )
    ap.add_argument("username", nargs="?", help="the account whose password to reset")
    ap.add_argument("--password", help="new password (omit to be prompted without echo)")
    ap.add_argument("--list", action="store_true", help="list all usernames and exit")
    args = ap.parse_args()

    conn, ph = connect()
    cur = conn.cursor()
    target = "Postgres (DATABASE_URL)" if USE_PG else f"local SQLite ({SQLITE_PATH})"
    print(f"Target: {target}")

    if args.list:
        cur.execute("SELECT username, is_admin FROM users ORDER BY username")
        rows = cur.fetchall()
        print("Accounts:" if rows else "No accounts found.")
        for username, is_admin in rows:
            print(f"  {username}{'  (admin)' if is_admin else ''}")
        conn.close()
        return

    if not args.username:
        conn.close()
        ap.error("provide a username, or use --list to see them")

    cur.execute(f"SELECT 1 FROM users WHERE username = {ph}", (args.username,))
    if not cur.fetchone():
        conn.close()
        sys.exit(f"No account named '{args.username}'. Use --list to see usernames.")

    password = args.password
    if not password:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Confirm password: "):
            conn.close()
            sys.exit("Passwords did not match.")
    if len(password) < 4:
        conn.close()
        sys.exit("Password too short (use at least 4 characters).")

    cur.execute(
        f"UPDATE users SET password_hash = {ph} WHERE username = {ph}",
        (generate_password_hash(password), args.username),
    )
    conn.commit()
    conn.close()
    print(f"Done. Password updated for '{args.username}' — log in with the new password.")


if __name__ == "__main__":
    main()
