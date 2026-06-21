# Deploying ScreamStream on Vercel + Neon (free, always-live)

This runs the app as **serverless functions on Vercel** (no always-on server to
sleep or run out of memory) with a **free Neon PostgreSQL** database (no 90-day
expiry). No credit card on either side.

The repo is already Vercel-ready:
- `vercel.json` — points Vercel's Python runtime at `app.py` and bundles the
  `templates/` and `static/` folders.
- `app.py` exposes the WSGI `app`, and its startup DB init is serverless-safe.

You do everything below yourself.

---

## 1. Create the free database (Neon)

1. Sign up at <https://neon.tech> (GitHub login works, no card).
2. **Create a project** → it gives you a Postgres database.
3. Open **Connection Details** and copy the **Pooled** connection string
   (the host contains `-pooler`). It looks like:
   `postgresql://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require`
   > Use the **pooled** one — serverless opens many short connections, and the
   > pooler keeps you under Neon's connection limit.

## 2. Load your catalog into Neon (one time, from your laptop)

```powershell
cd C:\Users\jackk\movie-site
pip install psycopg2-binary
$env:DATABASE_URL = "postgresql://...-pooler...neon.tech/neondb?sslmode=require"
python migrate_to_postgres.py
```

This copies all movies, users and watch history (~1-2 min). Re-running is safe.

## 3. Push the repo to GitHub

```powershell
cd C:\Users\jackk\movie-site
git add -A
git commit -m "Add Vercel serverless config"
git push origin main
```

## 4. Import the project on Vercel

1. Sign up at <https://vercel.com> (GitHub login, no card).
2. **Add New… → Project** → import your `screamstream` repo.
3. Framework preset: **Other** (the `vercel.json` already defines the build —
   leave the build/output settings empty).
4. Before the first deploy, open **Environment Variables** and add:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | your Neon **pooled** string (from step 1) |
   | `SECRET_KEY` | any long random string |
   | `ADMIN_USERNAME` | the username you want as admin |
   | `OMDB_API_KEY` | your OMDb key |
   | `GROQ_API_KEY` | your Groq key |
   | `RAPIDAPI_KEY` | your RapidAPI key |
   | `TMDB_REGION` | `IN` (or your country code) |
   | `YOUTUBE_API_KEY` | optional |

   > `DATABASE_URL` is the important one — set it **before** the first request so
   > the app uses Postgres (never the local file).

5. Click **Deploy**. After ~1-2 min you get a live URL like
   `https://screamstream.vercel.app`.

## 5. Test

Open the URL, create an account / log in, and browse. The first hit after a long
idle wakes Neon in ~1s; there is no 15-minute sleep and no 512 MB memory cap.

## Updating later

Just `git push origin main` — Vercel redeploys automatically. Data in Neon is
untouched.

## Good to know

- **Per-request time limit (~10s on the free plan).** Normal pages are fast. The
  *first* time a brand-new title is opened it fetches poster/trailer/scores,
  which can occasionally be slow — but it's cached after, so it's a one-time risk
  per title. Running `backfill_posters.py` against Neon ahead of time avoids it
  for popular titles.
- **No persistent disk** on serverless — all data lives in Neon (that's why
  `DATABASE_URL` must be set). The local `screamstream.db` is never used here.
- **Free, no expiry.** Vercel keeps Hobby projects deployed indefinitely; Neon's
  free database doesn't expire (it just auto-suspends when idle and resumes on
  the next query).
