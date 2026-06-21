import json
import mimetypes
import os
import re
import secrets
import sqlite3
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from html import escape as html_escape

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template, request,
    session, url_for
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "screamstream.db")

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras
    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError

def _load_env_file():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

_load_env_file()

app = Flask(__name__)
if USE_PG:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
if USE_PG and app.secret_key == "dev-secret-change-me":
    raise RuntimeError(
        "SECRET_KEY must be set in production — it signs the session cookies."
    )

app.config.update(
    SESSION_COOKIE_NAME="ss_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=USE_PG,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)

DUMMY_PASSWORD_HASH = generate_password_hash("timing-equalizer-not-a-real-password")
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_MAX_FAILURES = 10
MAX_PASSWORD_LEN = 128
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

COMMON_PASSWORDS = {
    "password", "12345678", "123456789", "1234567890", "qwerty", "qwertyuiop",
    "password1", "password123", "11111111", "00000000", "letmein", "iloveyou",
    "admin123", "welcome1", "abc12345", "qwerty123", "1q2w3e4r", "zaq12wsx",
    "football", "baseball", "sunshine", "princess", "dragon123", "monkey12",
    "superman", "trustno1", "passw0rd", "starwars", "whatever", "changeme",
}

def password_too_weak(password, username):
    low = password.lower()
    if low in COMMON_PASSWORDS:
        return ("That password is one of the most common ones around — please "
                "choose something less guessable.")
    if username and low == username.lower():
        return "Your password can't be the same as your username."
    if len(set(password)) <= 2:
        return ("That password is too simple (it repeats just a couple of "
                "characters) — please mix it up.")
    classes = sum(bool(re.search(p, password))
                  for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if len(password) < 12 and classes < 2:
        return ("That password is a bit weak — make it longer, or add a mix of "
                "letters, numbers and symbols.")
    return None

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "media-src 'self' https:; "
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    resp.headers["Content-Security-Policy"] = CSP
    if session.get("user_id") and "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    if USE_PG:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.before_request
def _csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRFToken", "")
        good = session.get("_csrf", "")
        if not good or not sent or not secrets.compare_digest(good, sent):
            flash("Your session timed out or the form couldn't be verified. "
                  "Please try that again.", "error")
            ref = request.referrer or ""
            if ref.startswith(request.host_url):
                return redirect(ref)
            return redirect(url_for("index"))


@app.context_processor
def _inject_csrf():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return {"csrf_token": token}


@app.context_processor
def _inject_watchlist():
    ids = set()
    if session.get("user_id"):
        try:
            ids = {r["movie_id"] for r in get_db().execute(
                "SELECT movie_id FROM watchlist WHERE user_id = ?",
                (session["user_id"],)).fetchall()}
        except Exception:
            ids = set()
    return {"watchlist_ids": ids}

GENRE_ORDER = ["Thriller", "Anime", "Horror"]

PLATFORM_SEARCH = {
    "Netflix": "https://www.netflix.com/search?q={q}",
    "Prime Video": "https://www.primevideo.com/search/?phrase={q}",
    "MX Player": "https://www.mxplayer.in/search?q={q}",
    "Disney+ Hotstar": "https://www.hotstar.com/in/explore?search_query={q}",
    "JustWatch": "https://www.justwatch.com/in/search?q={q}",
}

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
TMDB_REGION = os.environ.get("TMDB_REGION", "IN").upper()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"
TMDB_IMG_PROFILE = "https://image.tmdb.org/t/p/w185"

TMDB_GENRE_NAMES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Science Fiction", 53: "Thriller", 10752: "War", 37: "Western",
}
SORT_WHITELIST = {
    "popularity.desc", "vote_average.desc", "primary_release_date.desc",
    "revenue.desc", "vote_count.desc",
}

PROVIDER_ALIASES = {
    "Amazon Prime Video": "Prime Video",
    "Amazon Video": "Prime Video",
    "Disney Plus Hotstar": "Disney+ Hotstar",
    "JioHotstar": "Disney+ Hotstar",
    "Hotstar": "Disney+ Hotstar",
}

TMDB_DISCOVER = {
    name: f"&with_genres={gid}" for gid, name in TMDB_GENRE_NAMES.items()
}
TMDB_DISCOVER["Anime"] = "&with_genres=16&with_original_language=ja"

def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "movie"

def extract_yt_id(value):
    if not value:
        return ""
    value = value.strip()
    patterns = [
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
        r"/shorts/([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, value)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    return value

def yt_thumb(video_id):
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""

def yt_embed(video_id):
    return f"https://www.youtube.com/embed/{video_id}" if video_id else ""

def watch_links(movie):
    q = urllib.parse.quote(movie["title"])
    links = []
    names = [p.strip() for p in (movie["platforms"] or "").split(",") if p.strip()]
    for name in names:
        base = PLATFORM_SEARCH.get(name, PLATFORM_SEARCH["JustWatch"])
        links.append({"name": name, "url": base.format(q=q)})
    if not any(l["name"] == "JustWatch" for l in links):
        links.append({"name": "JustWatch",
                      "url": PLATFORM_SEARCH["JustWatch"].format(q=q)})
    return links

GENRE_COLORS = {
    "Action": ("#7f1d1d", "#b91c1c"), "Adventure": ("#14532d", "#16a34a"),
    "Animation": ("#4338ca", "#7c3aed"), "Anime": ("#4338ca", "#7c3aed"),
    "Comedy": ("#a16207", "#eab308"), "Crime": ("#1f2937", "#4b5563"),
    "Documentary": ("#0f766e", "#14b8a6"), "Drama": ("#1e3a8a", "#4338ca"),
    "Family": ("#0e7490", "#06b6d4"), "Fantasy": ("#6b21a8", "#a855f7"),
    "History": ("#78350f", "#b45309"), "Horror": ("#450a0a", "#991b1b"),
    "Music": ("#9d174d", "#db2777"), "Mystery": ("#312e81", "#4f46e5"),
    "Romance": ("#9f1239", "#e11d48"), "Science Fiction": ("#0c4a6e", "#0284c7"),
    "Thriller": ("#0f172a", "#334155"), "War": ("#3f3f46", "#71717a"),
    "Western": ("#7c2d12", "#c2410c"),
}
DEFAULT_COLORS = ("#15151f", "#2a2a38")

def _wrap(text, width):
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines

def title_card_svg(movie):
    title = movie["title"] or "Untitled"
    c1, c2 = GENRE_COLORS.get(movie["genre"], DEFAULT_COLORS)

    n = len(title)
    fs, width = (52, 13) if n <= 18 else (42, 17) if n <= 34 else (34, 22)
    lines = _wrap(title, width)[:4]
    line_h = fs * 1.15
    has_year = bool(movie["year"])
    block_h = len(lines) * line_h + (38 if has_year else 0)
    top = 375 - block_h / 2

    tspans = "".join(
        f'<tspan x="250" y="{top + fs + i * line_h:.0f}">{html_escape(ln)}</tspan>'
        for i, ln in enumerate(lines)
    )
    title_el = (
        f'<text text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
        f'font-weight="800" fill="#ffffff" font-size="{fs}">{tspans}</text>'
    )
    year_el = (
        f'<text x="250" y="{top + len(lines) * line_h + 30:.0f}" '
        f'text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
        f'font-size="30" fill="#ffffff" opacity="0.8">{movie["year"]}</text>'
        if has_year else ""
    )
    genre_el = (
        f'<text x="250" y="712" text-anchor="middle" letter-spacing="3" '
        f'font-family="Arial,Helvetica,sans-serif" font-size="22" '
        f'fill="#ffffff" opacity="0.65">{html_escape((movie["genre"] or "").upper())}</text>'
    )
    rating_el = ""
    if movie["imdb"]:
        rating_el = (
            '<rect x="22" y="22" rx="9" width="104" height="44" fill="#00000088"/>'
            f'<text x="40" y="52" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="24" font-weight="700" fill="#f5c518">'
            f'★ {movie["imdb"]:.1f}</text>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 750" '
        'width="500" height="750">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{c1}"/>'
        f'<stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
        '<rect width="500" height="750" fill="url(#g)"/>'
        '<rect width="500" height="750" fill="#000000" opacity="0.12"/>'
        f'{title_el}{year_el}{genre_el}{rating_el}</svg>'
    )

def _http_json(url, headers=None):
    try:
        h = {"User-Agent": "ScreamStream/1.0"}
        if headers:
            h.update(headers)
        req = Request(url, headers=h)
        with urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def _post_json(url, payload, headers=None, timeout=25):
    h = {"User-Agent": "ScreamStream/1.0", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    try:
        req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=h)
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        return None, f"HTTP {e.code}{(': ' + detail) if detail else ''}"
    except Exception as e:
        return None, str(e)

def map_genre(names):
    s = set(names or [])
    if "Animation" in s:
        return "Anime"
    if "Horror" in s:
        return "Horror"
    if s & {"Thriller", "Mystery", "Crime"}:
        return "Thriller"
    return names[0] if names else "Drama"

def pick_trailer(results):
    yt = [v for v in (results or []) if v.get("site") == "YouTube"]
    for ok in (
        lambda v: v.get("type") == "Trailer" and v.get("official"),
        lambda v: v.get("type") == "Trailer",
        lambda v: v.get("type") == "Teaser",
        lambda v: True,
    ):
        for v in yt:
            if ok(v):
                return v.get("key", "")
    return ""

def pick_providers(data, region):
    region_data = (data.get("results") or {}).get(region) or {}
    names = []
    for kind in ("flatrate", "ads", "free", "rent", "buy"):
        for p in region_data.get(kind, []):
            name = PROVIDER_ALIASES.get(p.get("provider_name", ""),
                                        p.get("provider_name", ""))
            if name and name not in names:
                names.append(name)
    return names[:6]

def parse_details(d):
    return {
        "title": d.get("title") or d.get("name") or "",
        "year": int(d["release_date"][:4]) if d.get("release_date") else None,
        "runtime": d.get("runtime") or None,
        "description": d.get("overview") or "",
        "poster": (TMDB_IMG + d["poster_path"]) if d.get("poster_path") else "",
        "genre": map_genre([g["name"] for g in d.get("genres", [])]),
        "imdb": round(d["vote_average"], 1) if d.get("vote_average") else None,
        "rotten": None,
        "mpaa": "",
        "imdb_id": d.get("imdb_id") or "",
        "release_date": d.get("release_date") or "",
        "status": d.get("status") or "",
        "tagline": d.get("tagline") or "",
        "budget": d.get("budget") or 0,
        "revenue": d.get("revenue") or 0,
    }

def parse_credits(credits):
    crew = credits.get("crew", []) if credits else []
    directors = [c["name"] for c in crew if c.get("job") == "Director"]
    cast = []
    for c in (credits.get("cast", []) if credits else [])[:12]:
        cast.append({
            "name": c.get("name", ""),
            "character": c.get("character", ""),
            "profile": (TMDB_IMG_PROFILE + c["profile_path"]) if c.get("profile_path") else "",
        })
    return ", ".join(directors), json.dumps(cast)

def parse_keywords(kw):
    items = (kw or {}).get("keywords") or (kw or {}).get("results") or []
    return ", ".join(k["name"] for k in items[:12])

def parse_omdb(data):
    out = {}
    if not data or data.get("Response") == "False":
        return out
    try:
        out["imdb"] = float(data.get("imdbRating"))
    except (TypeError, ValueError):
        pass
    for r in data.get("Ratings", []):
        if r.get("Source") == "Rotten Tomatoes":
            try:
                out["rotten"] = int(r["Value"].rstrip("%"))
            except (ValueError, KeyError):
                pass
    rated = data.get("Rated")
    if rated and rated != "N/A":
        out["mpaa"] = rated
    return out

def _trailer_query(title, year):
    return f"{title} {year} official trailer" if year else f"{title} official trailer"

def _youtube_search_scrape(query):
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        req = Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:
        return ""
    m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
    return m.group(1) if m else ""

def _youtube_search_api(query):
    if not YOUTUBE_API_KEY:
        return ""
    data = _http_json(
        "https://www.googleapis.com/youtube/v3/search?part=snippet&type=video"
        f"&videoEmbeddable=true&maxResults=1&q={urllib.parse.quote(query)}"
        f"&key={YOUTUBE_API_KEY}")
    for item in (data or {}).get("items", []):
        vid = (item.get("id") or {}).get("videoId")
        if vid:
            return vid
    return ""

def youtube_trailer(title, year):
    if not title:
        return ""
    query = _trailer_query(title, year)
    return _youtube_search_scrape(query) or _youtube_search_api(query)

def streaming_providers(imdb_id, region):
    if not (RAPIDAPI_KEY and imdb_id):
        return []
    data = _http_json(
        f"https://streaming-availability.p.rapidapi.com/shows/{imdb_id}",
        headers={
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": "streaming-availability.p.rapidapi.com",
        })
    if not data:
        return []
    options = (data.get("streamingOptions") or {}).get(region.lower()) or []
    names = []
    for opt in options:
        svc = opt.get("service") or {}
        raw = svc.get("name") or svc.get("id") or ""
        name = PROVIDER_ALIASES.get(raw, raw)
        if name and name not in names:
            names.append(name)
    return names[:6]

def tmdb_search(query):
    if not (TMDB_API_KEY and query):
        return []
    data = _http_json(
        f"{TMDB_API}/search/movie?api_key={TMDB_API_KEY}"
        f"&query={urllib.parse.quote(query)}&include_adult=false"
    )
    results = []
    for r in (data or {}).get("results", [])[:8]:
        results.append({
            "tmdb_id": r["id"],
            "title": r.get("title") or r.get("name") or "Untitled",
            "year": (r.get("release_date") or "")[:4],
            "poster": (TMDB_IMG + r["poster_path"]) if r.get("poster_path") else "",
            "overview": (r.get("overview") or "")[:170],
        })
    return results

def tmdb_import(tmdb_id):
    if not TMDB_API_KEY:
        return None
    details = _http_json(
        f"{TMDB_API}/movie/{tmdb_id}?api_key={TMDB_API_KEY}"
        "&append_to_response=videos,credits,keywords,watch/providers")
    if not details:
        return None

    info = parse_details(details)
    info["trailer"] = pick_trailer((details.get("videos") or {}).get("results", []))
    info["platforms"] = ", ".join(
        pick_providers(details.get("watch/providers") or {}, TMDB_REGION))
    info["director"], info["cast"] = parse_credits(details.get("credits") or {})
    info["keywords"] = parse_keywords(details.get("keywords"))

    imdb_id = info.pop("imdb_id", "")
    if OMDB_API_KEY and imdb_id:
        omdb = parse_omdb(_http_json(
            f"https://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"))
        info.update({k: v for k, v in omdb.items() if v is not None})
    return info

def discover_url(extra, page):
    return (f"{TMDB_API}/discover/movie?api_key={TMDB_API_KEY}"
            f"&sort_by=popularity.desc&include_adult=false&vote_count.gte=40"
            f"&page={page}{extra}")

def tmdb_bulk_import(genre_label, pages):
    if not TMDB_API_KEY:
        return 0
    upcoming = genre_label == "Upcoming"
    if upcoming:
        def page_url(page):
            return (f"{TMDB_API}/movie/upcoming?api_key={TMDB_API_KEY}"
                    f"&region={TMDB_REGION}&page={page}")
    else:
        extra = TMDB_DISCOVER.get(genre_label)
        if not extra:
            return 0
        def page_url(page):
            return discover_url(extra, page)

    db = get_db()
    existing = {row["tmdb_id"] for row in
                db.execute("SELECT tmdb_id FROM movies WHERE tmdb_id IS NOT NULL")}
    added = 0
    for page in range(1, max(1, pages) + 1):
        data = _http_json(page_url(page))
        if not data or not data.get("results"):
            break
        for r in data["results"]:
            tid = r.get("id")
            title = r.get("title") or r.get("name")
            if not tid or tid in existing or not title:
                continue
            poster = (TMDB_IMG + r["poster_path"]) if r.get("poster_path") else ""
            year = int(r["release_date"][:4]) if r.get("release_date") else None
            imdb = round(r["vote_average"], 1) if r.get("vote_average") else None
            if upcoming:
                names = [TMDB_GENRE_NAMES.get(g) for g in r.get("genre_ids", [])]
                genre = map_genre([n for n in names if n])
            else:
                genre = genre_label
            insert_movie(title, genre, year, "", imdb, None, None,
                         r.get("overview") or "", poster, "", "", "",
                         tmdb_id=tid, enriched=0,
                         release_date=r.get("release_date") or None)
            existing.add(tid)
            added += 1
    return added

def _row_get(row, key):
    return row[key] if key in row.keys() else None

def enrich_movie(movie):
    if movie["enriched"]:
        return movie
    if TMDB_API_KEY and movie["tmdb_id"]:
        return _enrich_tmdb(movie)
    if OMDB_API_KEY and _row_get(movie, "imdb_tt"):
        return _enrich_omdb(movie)
    return movie

def _enrich_omdb(movie):
    data = _http_json(
        f"https://www.omdbapi.com/?i={_row_get(movie, 'imdb_tt')}"
        f"&apikey={OMDB_API_KEY}&plot=full")
    db = get_db()
    if not data or data.get("Response") == "False":
        db.execute("UPDATE movies SET enriched = 1 WHERE id = ?", (movie["id"],))
        db.commit()
        return get_movie(movie["id"])

    def clean(val):
        return "" if not val or val == "N/A" else val

    scores = parse_omdb(data)
    poster = clean(data.get("Poster"))
    plot = clean(data.get("Plot"))
    director = clean(data.get("Director"))
    actors = clean(data.get("Actors"))
    cast = [{"name": n.strip(), "character": "", "profile": ""}
            for n in actors.split(",") if n.strip()]
    runtime = None
    rt_raw = (clean(data.get("Runtime")) or "").split(" ")[0]
    if rt_raw.isdigit():
        runtime = int(rt_raw)

    title = clean(data.get("Title")) or movie["title"]
    year = (clean(data.get("Year")) or "")[:4]
    trailer = youtube_trailer(title, year)
    platforms = ", ".join(streaming_providers(_row_get(movie, "imdb_tt"), TMDB_REGION))

    db.execute(
        """UPDATE movies SET
             poster = CASE WHEN ? <> '' THEN ? ELSE poster END,
             description = CASE WHEN ? <> '' THEN ? ELSE description END,
             imdb = COALESCE(?, imdb),
             rotten = COALESCE(?, rotten),
             mpaa = CASE WHEN ? <> '' THEN ? ELSE mpaa END,
             runtime = COALESCE(?, runtime),
             trailer = CASE WHEN ? <> '' THEN ? ELSE trailer END,
             platforms = CASE WHEN ? <> '' THEN ? ELSE platforms END,
             director = ?, "cast" = ?, enriched = 1
           WHERE id = ?""",
        (poster, poster, plot, plot, scores.get("imdb"), scores.get("rotten"),
         scores.get("mpaa", ""), scores.get("mpaa", ""), runtime,
         trailer, trailer, platforms, platforms,
         director, json.dumps(cast), movie["id"]),
    )
    db.commit()
    return get_movie(movie["id"])

def _enrich_tmdb(movie):
    info = tmdb_import(movie["tmdb_id"])
    db = get_db()
    if info:
        db.execute(
            """UPDATE movies SET
                 trailer = ?,
                 platforms = ?,
                 runtime = COALESCE(?, runtime),
                 mpaa = CASE WHEN ? <> '' THEN ? ELSE mpaa END,
                 imdb = COALESCE(?, imdb),
                 rotten = COALESCE(?, rotten),
                 description = CASE WHEN COALESCE(description,'')='' THEN ? ELSE description END,
                 poster = CASE WHEN COALESCE(poster,'')='' THEN ? ELSE poster END,
                 status = ?, tagline = ?, budget = ?, revenue = ?,
                 director = ?, "cast" = ?, keywords = ?,
                 release_date = COALESCE(NULLIF(?, ''), release_date),
                 enriched = 1
               WHERE id = ?""",
            (info.get("trailer", ""), info.get("platforms", ""),
             info.get("runtime"), info.get("mpaa", ""), info.get("mpaa", ""),
             info.get("imdb"), info.get("rotten"),
             info.get("description", ""), info.get("poster", ""),
             info.get("status", ""), info.get("tagline", ""),
             info.get("budget", 0), info.get("revenue", 0),
             info.get("director", ""), info.get("cast", "[]"),
             info.get("keywords", ""), info.get("release_date", ""),
             movie["id"]),
        )
    else:
        db.execute("UPDATE movies SET enriched = 1 WHERE id = ?", (movie["id"],))
    db.commit()
    return get_movie(movie["id"])

AI_SYSTEM = """You are a movie recommendation engine that channels the collective \
wisdom of film communities like Reddit (r/MovieSuggestions, r/movies, r/horror, \
r/TrueFilm, r/criterion) plus critic and audience consensus.

A user asks a question in plain English. Reply with ONLY a JSON object:
{
  "answer": "2-5 sentences in the honest, opinionated voice of seasoned movie \
fans giving advice. Explain WHY these are the picks - the vibe, what fans love, \
common comparisons. Conversational, no markdown headings or lists.",
  "titles": [{"title": "Exact Movie Title", "year": 2010}, ...]
}

Rules:
- titles: 8-15 REAL films that genuinely answer the question, best/consensus \
  favourites FIRST. Use each film's common English title and correct release \
  year so it can be matched to a catalog.
- Be accurate - only real movies that actually exist; never invent titles. \
  Honour every constraint in the question (genre, country, language, era, \
  rating, mood).
- For objective asks ("horror with IMDb above 7", "best Korean thrillers") still \
  return concrete title picks, not filters.
- Output ONLY the JSON object, nothing before or after it."""

def ai_enabled():
    return bool(GROQ_API_KEY)

def ai_query_plan(query):
    if not GROQ_API_KEY:
        return None, ("AI search needs a free Groq API key — get one at "
                      "console.groq.com and set GROQ_API_KEY in .env.")
    data, err = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {
            "model": GROQ_MODEL,
            "temperature": 0.7,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": AI_SYSTEM},
                {"role": "user", "content": query},
            ],
        },
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
    )
    if err:
        return None, f"AI search failed: {err}"
    try:
        plan = json.loads(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None, "AI search returned an unexpected response. Try rephrasing."
    plan.setdefault("answer", "")
    plan.setdefault("titles", [])
    return plan, None

def catalog_match(title, year=None):
    if not title or not title.strip():
        return None
    db = get_db()
    t = title.strip()
    rows = db.execute(
        "SELECT * FROM movies WHERE LOWER(title) = LOWER(?) "
        "ORDER BY imdb_votes DESC", (t,),
    ).fetchall()
    if not rows:
        rows = db.execute(
            "SELECT * FROM movies WHERE LOWER(title) LIKE LOWER(?) "
            "ORDER BY imdb_votes DESC LIMIT 8", (t + "%",),
        ).fetchall()
    if not rows:
        return None
    if year:
        try:
            y = int(year)
            for r in rows:
                if r["year"] == y:
                    return r
        except (TypeError, ValueError):
            pass
    return rows[0]

def tmdb_discover(params):
    q = [f"api_key={TMDB_API_KEY}", "include_adult=false", "vote_count.gte=25"]
    sort = (params.get("sort_by") or "popularity.desc")
    q.append("sort_by=" + (sort if sort in SORT_WHITELIST else "popularity.desc"))
    if params.get("with_genres"):
        q.append("with_genres=" + urllib.parse.quote(str(params["with_genres"])))
    if params.get("with_original_language"):
        q.append("with_original_language=" + urllib.parse.quote(str(params["with_original_language"])))
    if params.get("with_origin_country"):
        q.append("with_origin_country=" + urllib.parse.quote(str(params["with_origin_country"])))
    if params.get("release_year_gte"):
        q.append(f"primary_release_date.gte={int(params['release_year_gte'])}-01-01")
    if params.get("release_year_lte"):
        q.append(f"primary_release_date.lte={int(params['release_year_lte'])}-12-31")
    if params.get("vote_average_gte") is not None:
        q.append(f"vote_average.gte={params['vote_average_gte']}")
    data = _http_json(f"{TMDB_API}/discover/movie?" + "&".join(q))
    return (data or {}).get("results", [])[:18]

def resolve_title(title, year=None):
    if not title:
        return None
    url = (f"{TMDB_API}/search/movie?api_key={TMDB_API_KEY}"
           f"&query={urllib.parse.quote(title)}&include_adult=false")
    if year:
        url += f"&year={int(year)}"
    data = _http_json(url)
    res = (data or {}).get("results", [])
    return res[0] if res else None

def ensure_movie(item):
    tid = item.get("id")
    db = get_db()
    if tid:
        row = db.execute("SELECT * FROM movies WHERE tmdb_id = ?", (tid,)).fetchone()
        if row:
            return row
    title = item.get("title") or item.get("name")
    if not title:
        return None
    names = [TMDB_GENRE_NAMES.get(g) for g in (item.get("genre_ids") or [])]
    genre = map_genre([n for n in names if n])
    poster = (TMDB_IMG + item["poster_path"]) if item.get("poster_path") else ""
    year = int(item["release_date"][:4]) if item.get("release_date") else None
    imdb = round(item["vote_average"], 1) if item.get("vote_average") else None
    mid = insert_movie(title, genre, year, "", imdb, None, None,
                       item.get("overview") or "", poster, "", "", "",
                       tmdb_id=tid, enriched=0,
                       release_date=item.get("release_date") or None)
    return get_movie(mid)

def _pg_translate(sql):
    return sql.replace("?", "%s")

class _PgConn:

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if params:
            cur.execute(_pg_translate(sql), params)
        else:
            cur.execute(_pg_translate(sql))
        return cur

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()

def get_db():
    if "db" not in g:
        if USE_PG:
            raw = psycopg2.connect(DATABASE_URL)
            raw.autocommit = True
            g.db = _PgConn(raw)
        else:
            g.db = sqlite3.connect(DATABASE)
            g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS movies (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    genre        TEXT NOT NULL,
    year         INTEGER,
    mpaa         TEXT,
    imdb         REAL,
    rotten       INTEGER,
    runtime      INTEGER,
    description  TEXT,
    poster       TEXT,
    trailer      TEXT,
    video        TEXT,
    platforms    TEXT,
    tmdb_id      INTEGER,
    enriched     INTEGER NOT NULL DEFAULT 0,
    release_date TEXT,
    status       TEXT,
    tagline      TEXT,
    budget       BIGINT,
    revenue      BIGINT,
    director     TEXT,
    "cast"       TEXT,
    keywords     TEXT,
    imdb_tt      TEXT,
    imdb_votes   INTEGER
);
CREATE TABLE IF NOT EXISTS history (
    user_id    INTEGER NOT NULL,
    movie_id   TEXT NOT NULL,
    watched_at TEXT NOT NULL,
    PRIMARY KEY (user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS watchlist (
    user_id  INTEGER NOT NULL,
    movie_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id           SERIAL PRIMARY KEY,
    ip           TEXT NOT NULL,
    username     TEXT,
    attempted_at TEXT NOT NULL,
    success      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_movies_genre_votes ON movies(genre, imdb_votes);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts(ip, attempted_at);
"""

def init_db():
    if USE_PG:
        raw = psycopg2.connect(DATABASE_URL)
        raw.autocommit = True
        with raw.cursor() as cur:
            cur.execute(PG_SCHEMA)
        raw.close()
        return
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS movies (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            genre       TEXT NOT NULL,
            year        INTEGER,
            mpaa        TEXT,
            imdb        REAL,
            rotten      INTEGER,
            runtime     INTEGER,
            description TEXT,
            poster      TEXT,
            trailer     TEXT,   -- YouTube video id ("" if none)
            video       TEXT,   -- direct .mp4 URL if streamable here, else ""
            platforms   TEXT,   -- comma-separated platform names
            tmdb_id     INTEGER,-- source TMDB id (NULL for manual entries)
            enriched    INTEGER NOT NULL DEFAULT 0, -- trailer/providers fetched?
            release_date TEXT,   -- full ISO date (for upcoming/coming-soon)
            status      TEXT,    -- Released / Post Production / Planned / ...
            tagline     TEXT,
            budget      INTEGER,
            revenue     INTEGER,
            director    TEXT,
            cast        TEXT,    -- JSON list of {name, character, profile}
            keywords    TEXT     -- comma-separated tags
        );

        CREATE TABLE IF NOT EXISTS history (
            user_id    INTEGER NOT NULL,
            movie_id   TEXT NOT NULL,
            watched_at TEXT NOT NULL,
            PRIMARY KEY (user_id, movie_id)
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            user_id  INTEGER NOT NULL,
            movie_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, movie_id)
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ip           TEXT NOT NULL,
            username     TEXT,
            attempted_at TEXT NOT NULL,
            success      INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    cols = {row[1] for row in db.execute("PRAGMA table_info(movies)")}
    add = {
        "tmdb_id": "INTEGER",
        "enriched": "INTEGER NOT NULL DEFAULT 0",
        "release_date": "TEXT",
        "status": "TEXT",
        "tagline": "TEXT",
        "budget": "INTEGER",
        "revenue": "INTEGER",
        "director": "TEXT",
        "cast": "TEXT",
        "keywords": "TEXT",
        "imdb_tt": "TEXT",
        "imdb_votes": "INTEGER",
    }
    for name, decl in add.items():
        if name not in cols:
            db.execute(f"ALTER TABLE movies ADD COLUMN {name} {decl}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_movies_genre_votes "
               "ON movies(genre, imdb_votes)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time "
               "ON login_attempts(ip, attempted_at)")
    db.commit()
    db.close()

def seed_movies():
    db = get_db()
    if db.execute("SELECT COUNT(*) AS n FROM movies").fetchone()["n"] > 0:
        return

    sample = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/"

    seed = [
        ("Tears of Steel", "Thriller", 2012, "PG-13", 6.7, 73, 12,
         "Warriors and scientists battle rogue machines to save a future "
         "Amsterdam. A Blender open-movie sci-fi thriller.",
         "R6MlUcmOul8", sample + "TearsOfSteel.mp4", ""),
        ("Sintel", "Anime", 2010, "PG", 7.5, 80, 15,
         "A lone warrior crosses a hostile world searching for the dragon she "
         "once raised. Award-winning animated short.",
         "eRsGyueVLvQ", sample + "Sintel.mp4", ""),
        ("Big Buck Bunny", "Anime", 2008, "G", 6.3, 75, 10,
         "A big-hearted rabbit gets even with three bullying rodents in this "
         "classic animated comedy short.",
         "aqz-KE-bpKQ", sample + "BigBuckBunny.mp4", ""),

        
        ("Inception", "Thriller", 2010, "PG-13", 8.8, 87, 148,
         "A thief who steals corporate secrets through dream-sharing is given "
         "the inverse task of planting an idea into a target's mind.",
         "YoHD9XEInc0", "", "Netflix, Prime Video"),
        ("Parasite", "Thriller", 2019, "R", 8.5, 99, 132,
         "A poor family schemes to become employed by a wealthy household, "
         "with darkly comic and tragic results.",
         "5xH0HfJHsaY", "", "Prime Video, MX Player"),
        ("Get Out", "Horror", 2017, "R", 7.8, 98, 104,
         "A young Black man visits his white girlfriend's family estate and "
         "uncovers a disturbing secret.",
         "DzfpyUB60YY", "", "Netflix, Prime Video"),
        ("Hereditary", "Horror", 2018, "R", 7.3, 90, 127,
         "After the family matriarch dies, her daughter's household begins to "
         "unravel terrifying ancestral secrets.",
         "V6wWKNij_1M", "", "Prime Video, MX Player"),
        ("A Quiet Place", "Horror", 2018, "PG-13", 7.5, 96, 90,
         "A family must live in silence to avoid blind creatures that hunt by "
         "sound.",
         "WR7cc5t7tv8", "", "Netflix, Prime Video"),
        ("The Conjuring", "Horror", 2013, "R", 7.5, 86, 112,
         "Paranormal investigators help a family terrorized by a dark presence "
         "in their farmhouse.",
         "k10ETZ41q5o", "", "Netflix, MX Player"),
        ("Spirited Away", "Anime", 2001, "PG", 8.6, 97, 125,
         "A young girl wanders into a world of spirits and must work to free "
         "herself and her parents.",
         "ByXuk9QqQkk", "", "Netflix, Prime Video"),
        ("Your Name", "Anime", 2016, "PG", 8.4, 98, 106,
         "Two teenagers who have never met find themselves mysteriously "
         "swapping bodies across distance and time.",
         "xU47nhruN-Q", "", "Prime Video, MX Player"),
        ("Demon Slayer: Mugen Train", "Anime", 2020, "R", 8.2, 96, 117,
         "Tanjiro and the Flame Hashira board a train where dozens have "
         "vanished, confronting a powerful demon.",
         "ofb0aBrZD3Q", "", "Netflix, Prime Video"),
    ]

    for row in seed:
        (title, genre, year, mpaa, imdb, rotten, runtime,
         desc, trailer, video, platforms) = row
        db.execute(
            """INSERT INTO movies
               (id, title, genre, year, mpaa, imdb, rotten, runtime,
                description, poster, trailer, video, platforms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (slugify(title), title, genre, year, mpaa, imdb, rotten, runtime,
             desc, yt_thumb(trailer), trailer, video, platforms),
        )
    db.commit()

def get_movie(movie_id):
    return get_db().execute(
        "SELECT * FROM movies WHERE id = ?", (movie_id,)
    ).fetchone()

def insert_movie(title, genre, year, mpaa, imdb, rotten, runtime,
                 description, poster, trailer, video, platforms,
                 tmdb_id=None, enriched=1, release_date=None):
    db = get_db()
    base = slugify(title)
    movie_id, n = base, 2
    while db.execute("SELECT 1 FROM movies WHERE id = ?", (movie_id,)).fetchone():
        movie_id = f"{base}-{n}"
        n += 1
    db.execute(
        """INSERT INTO movies
           (id, title, genre, year, mpaa, imdb, rotten, runtime,
            description, poster, trailer, video, platforms, tmdb_id, enriched,
            release_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (movie_id, title, genre, year, mpaa, imdb, rotten, runtime,
         description, poster, trailer, video, platforms, tmdb_id, enriched,
         release_date),
    )
    db.commit()
    return movie_id

def genres_in_order(present):
    ordered = [g_ for g_ in GENRE_ORDER if g_ in present]
    ordered += sorted(g_ for g_ in present if g_ not in GENRE_ORDER)
    return ordered

def _client_ip():
    return (request.remote_addr or "unknown")[:64]

def _login_locked(db, ip):
    cutoff = (datetime.now(timezone.utc) - LOGIN_WINDOW).isoformat()
    db.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))
    db.commit()
    n = db.execute(
        "SELECT COUNT(*) AS n FROM login_attempts "
        "WHERE ip = ? AND success = 0 AND attempted_at >= ?",
        (ip, cutoff),
    ).fetchone()["n"]
    return n >= LOGIN_MAX_FAILURES

def _record_login(db, ip, username, success):
    db.execute(
        "INSERT INTO login_attempts (ip, username, attempted_at, success) "
        "VALUES (?, ?, ?, ?)",
        (ip, (username or "")[:150], datetime.now(timezone.utc).isoformat(),
         1 if success else 0),
    )
    db.commit()

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        if not session.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not password:
            flash("Please fill in both a username and a password.", "error")
        elif len(username) < 3 or len(username) > 32:
            flash("Username must be between 3 and 32 characters long.", "error")
        elif not re.search(r"[A-Za-z]", username):
            flash("Username must include at least one letter — it can't be only "
                  "numbers or symbols.", "error")
        elif not USERNAME_RE.match(username):
            flash("Username can only use letters, numbers and the symbols . _ - "
                  "(no spaces, @, # and the like).", "error")
        elif len(password) < 8:
            flash("Password is too short — please use at least 8 characters.",
                  "error")
        elif len(password) > MAX_PASSWORD_LEN:
            flash("Password is too long — please keep it under 128 characters.",
                  "error")
        elif password != confirm:
            flash("The two passwords don't match — please retype them.", "error")
        elif (weak := password_too_weak(password, username)):
            flash(weak, "error")
        else:
            db = get_db()
            is_admin = username == ADMIN_USERNAME
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash, is_admin) "
                    "VALUES (?, ?, ?)",
                    (username, generate_password_hash(password),
                     1 if is_admin else 0),
                )
                db.commit()
            except IntegrityError:
                flash("That username is already taken — please pick another.",
                      "error")
            else:
                if is_admin:
                    flash("Admin account created — please log in.", "success")
                else:
                    flash("Account created — please log in.", "success")
                return redirect(url_for("login"))

    return render_template("register.html",
                           username=request.form.get("username", ""))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        ip = _client_ip()
        if _login_locked(db, ip):
            flash("Too many failed attempts from this device. Please wait a few "
                  "minutes and try again.", "error")
            return render_template("login.html", username=username), 429

        user = None
        if len(password) <= MAX_PASSWORD_LEN:
            user = db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()

        if user:
            valid = check_password_hash(user["password_hash"], password)
        else:
            check_password_hash(DUMMY_PASSWORD_HASH, password)
            valid = False

        if valid:
            _record_login(db, ip, username, True)
            should_admin = user["username"] == ADMIN_USERNAME
            if bool(user["is_admin"]) != should_admin:
                db.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                           (1 if should_admin else 0, user["id"]))
                db.commit()
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = should_admin
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("index"))

        _record_login(db, ip, username, False)
        flash("We couldn't sign you in — the username or password is incorrect.",
              "error")

    return render_template("login.html",
                           username=request.form.get("username", ""))

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))

ROW_LIMIT = 24     
PAGE_SIZE = 60      

@app.route("/")
@login_required
def index():
    db = get_db()
    active = request.args.get("genre", "All")
    query = request.args.get("q", "").strip()

    present = {m["genre"] for m in db.execute("SELECT DISTINCT genre FROM movies")}
    genre_list = genres_in_order(present)

    if active != "All" or query:
        clauses, params = [], []
        if active != "All":
            clauses.append("genre = ?")
            params.append(active)
        if query:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ?)")
            like = f"%{query.lower()}%"
            params += [like, like]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        total = db.execute(
            "SELECT COUNT(*) AS n FROM movies" + where, params).fetchone()["n"]
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        try:
            page = min(max(int(request.args.get("page", 1)), 1), pages)
        except ValueError:
            page = 1

        movies = db.execute(
            "SELECT * FROM movies" + where
            + " ORDER BY imdb_votes DESC, imdb DESC LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()

        return render_template(
            "index.html", mode="grid", genres=genre_list, active=active,
            query=query, movies=movies, total=total, page=page, pages=pages,
        )
    rows = []
    for g_ in genre_list:
        items = db.execute(
            "SELECT * FROM movies WHERE genre = ? "
            "ORDER BY imdb_votes DESC, imdb DESC LIMIT ?",
            (g_, ROW_LIMIT),
        ).fetchall()
        if items:
            count = db.execute(
                "SELECT COUNT(*) AS n FROM movies WHERE genre = ?", (g_,)
            ).fetchone()["n"]
            rows.append({"genre": g_, "items": items, "count": count})

    continue_watching = db.execute(
        """SELECT m.* FROM history h JOIN movies m ON m.id = h.movie_id
           WHERE h.user_id = ? ORDER BY h.watched_at DESC LIMIT 20""",
        (session["user_id"],),
    ).fetchall()

    my_list = db.execute(
        """SELECT m.* FROM watchlist w JOIN movies m ON m.id = w.movie_id
           WHERE w.user_id = ? ORDER BY w.added_at DESC LIMIT 20""",
        (session["user_id"],),
    ).fetchall()

    coming_soon = db.execute(
        """SELECT * FROM movies WHERE release_date IS NOT NULL AND release_date > ?
           ORDER BY release_date ASC LIMIT 20""",
        (date.today().isoformat(),),
    ).fetchall()

    return render_template(
        "index.html", mode="rows", genres=genre_list, active=active,
        query=query, rows=rows, continue_watching=continue_watching,
        my_list=my_list, coming_soon=coming_soon,
    )

@app.route("/ask")
@login_required
def ask():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("index"))

    if not ai_enabled():
        flash("AI search needs a free Groq API key — set GROQ_API_KEY in .env "
              "(get one at console.groq.com). Showing a basic title search.",
              "error")
        return redirect(url_for("index", q=query))

    plan, err = ai_query_plan(query)
    if err:
        flash(err, "error")
        return redirect(url_for("index", q=query))

    results, seen = [], set()
    for t in (plan.get("titles") or [])[:15]:
        m = catalog_match(t.get("title", ""), t.get("year"))
        if m and m["id"] not in seen:
            seen.add(m["id"])
            results.append(m)

    return render_template(
        "ask.html", query=query, answer=plan.get("answer", ""), results=results,
    )

@app.route("/poster/<movie_id>.svg")
@login_required
def poster(movie_id):
    movie = get_movie(movie_id)
    if movie is None:
        abort(404)
    resp = Response(title_card_svg(movie), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.route("/watch/<movie_id>")
@login_required
def watch(movie_id):
    movie = get_movie(movie_id)
    if movie is None:
        abort(404)

    movie = enrich_movie(movie)

    db = get_db()
    db.execute(
        """INSERT INTO history (user_id, movie_id, watched_at) VALUES (?, ?, ?)
           ON CONFLICT(user_id, movie_id)
           DO UPDATE SET watched_at = excluded.watched_at""",
        (session["user_id"], movie_id, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()

    related = db.execute(
        "SELECT * FROM movies WHERE genre = ? AND id != ? ORDER BY year DESC LIMIT 8",
        (movie["genre"], movie["id"]),
    ).fetchall()

    try:
        cast = json.loads(movie["cast"]) if movie["cast"] else []
    except (json.JSONDecodeError, TypeError):
        cast = []
    upcoming = bool(movie["release_date"]
                    and movie["release_date"] > date.today().isoformat())

    return render_template(
        "watch.html",
        movie=movie,
        related=related,
        cast=cast,
        upcoming=upcoming,
        embed=yt_embed(movie["trailer"]),
        links=watch_links(movie),
        yt_search="https://www.youtube.com/results?search_query="
                  + urllib.parse.quote(movie["title"] + " trailer"),
    )

@app.route("/history")
@login_required
def history():
    rows = get_db().execute(
        """SELECT m.*, h.watched_at FROM history h JOIN movies m ON m.id = h.movie_id
           WHERE h.user_id = ? ORDER BY h.watched_at DESC""",
        (session["user_id"],),
    ).fetchall()
    return render_template("history.html", movies=rows)

@app.route("/history/clear", methods=["POST"])
@login_required
def clear_history():
    db = get_db()
    db.execute("DELETE FROM history WHERE user_id = ?", (session["user_id"],))
    db.commit()
    flash("Watch history cleared.", "success")
    return redirect(url_for("history"))

@app.route("/watchlist")
@login_required
def watchlist():
    rows = get_db().execute(
        """SELECT m.*, w.added_at FROM watchlist w JOIN movies m ON m.id = w.movie_id
           WHERE w.user_id = ? ORDER BY w.added_at DESC""",
        (session["user_id"],),
    ).fetchall()
    return render_template("watchlist.html", movies=rows)

@app.route("/watchlist/toggle/<movie_id>", methods=["POST"])
@login_required
def watchlist_toggle(movie_id):
    db = get_db()
    uid = session["user_id"]
    already = db.execute(
        "SELECT 1 FROM watchlist WHERE user_id = ? AND movie_id = ?",
        (uid, movie_id),
    ).fetchone()
    if already:
        db.execute("DELETE FROM watchlist WHERE user_id = ? AND movie_id = ?",
                   (uid, movie_id))
        db.commit()
        flash("Removed from My List.", "success")
    else:
        if get_movie(movie_id) is None:
            abort(404)
        db.execute(
            "INSERT INTO watchlist (user_id, movie_id, added_at) VALUES (?, ?, ?)",
            (uid, movie_id, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
        flash("Added to My List.", "success")
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("watchlist"))

@app.route("/watchlist/clear", methods=["POST"])
@login_required
def clear_watchlist():
    db = get_db()
    db.execute("DELETE FROM watchlist WHERE user_id = ?", (session["user_id"],))
    db.commit()
    flash("Your list has been cleared.", "success")
    return redirect(url_for("watchlist"))

@app.route("/admin")
@admin_required
def admin():
    tmdb_q = request.args.get("tmdb_q", "").strip()
    results = tmdb_search(tmdb_q) if tmdb_q else []
    movies = get_db().execute(
        "SELECT * FROM movies ORDER BY genre, title").fetchall()
    return render_template(
        "admin.html",
        movies=movies,
        platforms=list(PLATFORM_SEARCH.keys()),
        tmdb_enabled=bool(TMDB_API_KEY),
        omdb_enabled=bool(OMDB_API_KEY),
        region=TMDB_REGION,
        tmdb_q=tmdb_q,
        results=results,
        bulk_genres=list(TMDB_DISCOVER.keys()) + ["Upcoming"],
    )

@app.route("/admin/import", methods=["POST"])
@admin_required
def admin_import():
    if not TMDB_API_KEY:
        flash("TMDB is not configured. Set TMDB_API_KEY first.", "error")
        return redirect(url_for("admin"))

    tmdb_id = request.form.get("tmdb_id", "").strip()
    info = tmdb_import(tmdb_id)
    if not info or not info.get("title"):
        flash("Couldn't import from TMDB — check your API key / connection.",
              "error")
        return redirect(url_for("admin"))

    insert_movie(
        info["title"], info["genre"], info.get("year"), info.get("mpaa", ""),
        info.get("imdb"), info.get("rotten"), info.get("runtime"),
        info.get("description", ""), info.get("poster", ""),
        info.get("trailer", ""), "", info.get("platforms", ""),
        tmdb_id=int(tmdb_id) if tmdb_id.isdigit() else None, enriched=1,
    )
    flash(f"Imported “{info['title']}” from TMDB.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/bulk", methods=["POST"])
@admin_required
def admin_bulk():
    if not TMDB_API_KEY:
        flash("TMDB is not configured. Set TMDB_API_KEY first.", "error")
        return redirect(url_for("admin"))

    genres = request.form.getlist("genres")
    try:
        pages = int(request.form.get("pages", 3))
    except ValueError:
        pages = 3
    pages = min(max(pages, 1), 50)  

    total = sum(tmdb_bulk_import(g, pages) for g in genres)
    if total:
        flash(f"Imported {total} new title(s) from TMDB.", "success")
    else:
        flash("No new titles imported (already in catalog or none found).",
              "error")
    return redirect(url_for("admin"))

@app.route("/admin/add", methods=["POST"])
@admin_required
def admin_add():
    f = request.form
    title = f.get("title", "").strip()
    genre = f.get("genre", "").strip() or "Thriller"
    if not title:
        flash("Title is required.", "error")
        return redirect(url_for("admin"))

    def num(name, cast):
        raw = f.get(name, "").strip()
        try:
            return cast(raw) if raw else None
        except ValueError:
            return None

    trailer = extract_yt_id(f.get("trailer", ""))
    poster = f.get("poster", "").strip() or yt_thumb(trailer)

    insert_movie(
        title, genre, num("year", int), f.get("mpaa", "").strip(),
        num("imdb", float), num("rotten", int), num("runtime", int),
        f.get("description", "").strip(), poster, trailer,
        f.get("video", "").strip(), f.get("platforms", "").strip(),
    )
    flash(f"Added “{title}”.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/delete/<movie_id>", methods=["POST"])
@admin_required
def admin_delete(movie_id):
    db = get_db()
    db.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    db.execute("DELETE FROM history WHERE movie_id = ?", (movie_id,))
    db.execute("DELETE FROM watchlist WHERE movie_id = ?", (movie_id,))
    db.commit()
    flash("Movie deleted.", "success")
    return redirect(url_for("admin"))

@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(429)
@app.errorhandler(500)
def _handle_error(err):
    code = getattr(err, "code", 500)
    messages = {
        400: "We couldn't process that request. Your session may have expired — "
             "please go back and try again.",
        403: "You don't have permission to open that page.",
        404: "We couldn't find that page or title. The link may be old or mistyped.",
        413: "That upload is too large — please choose a smaller file.",
        429: "You're doing that a little too fast. Please wait a minute and try "
             "again.",
        500: "Something went wrong on our side. Please try again in a moment.",
    }
    return render_template("error.html", code=code,
                           message=messages.get(code, "Unexpected error.")), code

_db_ready = False

def _ensure_db_ready():
    global _db_ready
    if not _db_ready:
        init_db()
        seed_movies()
        _db_ready = True

@app.before_request
def _bootstrap_db():
    if not _db_ready:
        _ensure_db_ready()

with app.app_context():
    try:
        _ensure_db_ready()
    except Exception:
        pass

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
