# ScreamStream 🎬

A movie exploring web app: sign up, browse a **144,000-title**
catalog by genre, stream the free in-app titles, or jump to the trailer +
"where to watch" links for everything else. An **"Ask anything"** box answers
natural-language questions with real films from the catalog.

### 🔗 Live demo — **https://screamstream-dae0.onrender.com**

> Hosted on Render's free tier. If the site's been idle, the **first load takes
> ~30–60s to wake up** — that's normal. Create an account to browse.

---

## Highlights

What makes this more than a CRUD demo:

- **Dual-mode database, one code path.** A small query-translation shim lets the
  exact same data-access code run on **SQLite** locally and **PostgreSQL** in
  production — switched purely by whether `DATABASE_URL` is set. No ORM, no
  separate prod/dev branches to drift apart.
- **144k-title catalog, built cheaply.** Movies are bulk-imported from IMDb's
  official datasets (no per-movie API calls). Expensive details — poster,
  trailer, full scores, cast, where-to-watch — are fetched **lazily on first
  open** and cached in the row, so the import stays fast and external APIs aren't
  hammered.
- **Respects rate limits by design.** A backfill script fills real posters
  **most-popular-first** (the order the grids actually render) within OMDb's
  daily cap, so the visible catalog looks complete first.
- **LLM grounded in the real catalog.** "Ask anything" sends the question to a
  free LLM, then matches the titles it names back against the local database — so
  every answer links to a real detail page, not a hallucinated one.
- **Graceful degradation.** Every external integration is optional; without a key
  the feature falls back to a simpler behaviour and the site still runs.

---

## Features

| | |
|---|---|
| **Accounts** | Register / log in; passwords stored only as salted hashes. |
| **Huge catalog** | ~144k movies from IMDb datasets, organised into genre rows. |
| **Rich metadata** | IMDb / Rotten Tomatoes / Metacritic scores, year, rating, runtime, synopsis, cast, director. |
| **Stream or trailer** | Free titles play in an HTML5 player; everything else embeds its YouTube trailer. |
| **Where to watch** | Region-accurate streaming-provider links per title. |
| **Watch history** | "Continue Watching" row + a dedicated history page. |
| **Ask anything** | Plain-English movie questions answered with real catalog matches. |
| **Admin panel** | Add / remove / import movies, behind a single admin account. |

---

## Tech stack

- **Python 3.12** · **Flask 3** · **Jinja2** — server-rendered; *no* JS framework.
- **SQLite** (dev) / **PostgreSQL via psycopg2** (prod) — same code, dual-mode.
- **Werkzeug** password hashing (PBKDF2) · **Gunicorn** WSGI server.
- **HTML5 + a single hand-written CSS file** — no CSS framework; `<video>` for
  streams, YouTube `<iframe>` for trailers.
- **Data:** IMDb datasets (catalog) · OMDb (posters/scores) · YouTube (trailers) ·
  Streaming Availability API (where-to-watch) · Groq LLM (Ask anything).
- **Hosting:** Render web service + free managed PostgreSQL (`render.yaml` Blueprint).

---

## Run it locally

No build step — Python is interpreted; the only "build" is installing deps.

```bash
git clone https://github.com/Jyotishman89/screamstream.git
cd screamstream
python -m venv .venv && .venv\Scripts\Activate.ps1   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: add API keys for posters / trailers / Ask-anything
python app.py
```

Open **http://127.0.0.1:5000**, create an account, and browse. The app creates
its tables and seeds a few sample titles on first run, so it works immediately.

**Load the full catalog** (optional):

```bash
python import_imdb.py 100     # import ~144k movies (arg = min vote count)
python backfill_posters.py    # fill real posters, most-popular-first
```

Configuration is via environment variables — see **`.env.example`** for the full
list (all optional; the app degrades gracefully without them).

---

## Deployment

The repo ships a **Render Blueprint** (`render.yaml`) that provisions the web
service **and** a free PostgreSQL database, wiring its connection string into the
app as `DATABASE_URL`. After deploy, load the catalog into Postgres once with
`python migrate_to_postgres.py`. Full walkthrough: **[`DEPLOY.md`](DEPLOY.md)**.

> Render's free Postgres expires ~90 days after creation — recreate and re-run
> the migration, or upgrade to a paid instance. Details in `DEPLOY.md`.

---

## Security & content notes

- **No credentials in the repo.** API keys, the admin username and all passwords
  live only in environment variables / the database — never committed.
- **Passwords hashed** with Werkzeug (PBKDF2); plaintext is never stored.
  Sessions are signed with `SECRET_KEY`. Flask debug is off unless explicitly enabled.
- **Content:** real films can't be bundled, so **▶ Stream** titles use free
  Creative-Commons videos (Blender open movies); everything else links to the
  real trailer and streaming platforms. Scores and years are real reference data.
