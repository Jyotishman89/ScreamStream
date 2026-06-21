# Deploying ScreamStream (free, permanent URL)

This deploys to **Render** (free tier, full outbound internet so Groq / OMDb /
RapidAPI / YouTube all work). Everything is pre-configured — you only push to
GitHub and click through Render.

## 1. Push to a **private** GitHub repo

The catalog database (`screamstream.db`, with your admin account) ships in the
repo, so make the repo **Private**.

A local git repo with everything committed has already been prepared in this
folder. Create an empty repo on GitHub (no README), then:

```powershell
cd C:\Users\jackk\movie-site
git remote add origin https://github.com/<you>/screamstream.git
git branch -M main
git push -u origin main
```

## 2. Create the service on Render

1. Sign up / log in at <https://render.com> (free, GitHub login works).
2. **New +  →  Blueprint**, pick your `screamstream` repo. Render reads
   `render.yaml` and creates **two** resources: the web service **and a free
   Postgres database** (`screamstream-db`). The database's connection string is
   wired into the app automatically as `DATABASE_URL` — you don't copy it.
3. When prompted, fill the three secret values (they are **not** in the repo):
   - `OMDB_API_KEY`
   - `GROQ_API_KEY`
   - `RAPIDAPI_KEY`
   (Copy them from your local `.env`.) `SECRET_KEY` is auto-generated;
   `DATABASE_URL`, `ADMIN_USERNAME`, `TMDB_REGION`, `PYTHON_VERSION` are set for you.
4. **Apply / Create** → first build takes ~2-3 min. On first boot the app creates
   the Postgres tables itself (and seeds ~12 starter films).

Your site goes live at `https://screamstream.onrender.com` (or the name Render
gives it) — but the catalog is nearly empty until you run the one-time data
load below.

> No Blueprint? Do it manually: **New + → PostgreSQL** (free) to create the DB,
> then **New + → Web Service** → connect repo → Runtime *Python* →
> Build `pip install -r requirements.txt` →
> Start `gunicorn app:app --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:$PORT`
> → add the same env vars, plus `DATABASE_URL` = the database's **Internal**
> connection string.

## 3. Load your 144k-movie catalog into Postgres (one time)

The catalog lives in your local `screamstream.db`. Copy it up to the new
Postgres database from your laptop:

1. In Render, open the `screamstream-db` database → copy its **External**
   connection URL (`postgres://…`).
2. From the project folder:

   ```powershell
   cd C:\Users\jackk\movie-site
   pip install psycopg2-binary           # one-time, for your local Python
   $env:DATABASE_URL = "postgres://USER:PASS@HOST/DB"   # the External URL
   python migrate_to_postgres.py
   ```

   It bulk-copies all movies, users and watch history (~1-2 min). Re-running is
   safe — existing rows are skipped, so it also tops up newly imported titles.

Now log in to the live site with your admin account (the username you set in
`ADMIN_USERNAME`) — the full catalog is there.

## Good to know (free tier)

- **Durable data:** sign-ups, watch history and lazily-cached posters/trailers
  now live in Postgres, so they **survive restarts and redeploys**. The
  `screamstream.db` in the repo is only the local/source catalog now — the
  deployed app never reads it.
- **Free Postgres expires after 90 days:** Render deletes free databases ~90
  days after creation. Before then, create a fresh free DB, point
  `DATABASE_URL` at it, and re-run `migrate_to_postgres.py` (the catalog
  reloads; live user data on the old DB would need a manual dump first — ask and
  I'll script it). A paid Postgres instance ($7/mo) removes the expiry.
- **Sleeps when idle:** after ~15 min of no traffic the web instance spins down;
  the next visit takes ~30-60s to wake. Paid plans stay always-on.
- **Secrets** live as Render env vars, never in the repo. `.env` stays local.
- **Updating the site:** `git push` → Render auto-deploys (data is untouched).
