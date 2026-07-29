# Container image for offdutylocks.com (Railway).
#
# Railway's Railpack builder can't infer a start command for this project
# ("No start command detected"), so this Dockerfile makes the build
# deterministic and supplies the start command explicitly. The same image also
# carries the `wnba-pipeline` CLI, which the GitHub Actions scrapers invoke.
FROM python:3.11-slim

# The runner emits the run manifest as a single JSON line on stdout and
# structured logs on stderr; unbuffered output keeps both live in Railway logs.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the whole project. fixtures/ MUST be present at runtime: the expected
# WNBA team set is resolved from fixtures/expected_teams/<season>.json (it is
# never hardcoded), and offline/fixture runs read fixtures/sanitized/.
COPY . /app

# Editable install, deliberately. It puts the `wnba-pipeline` console script on
# PATH AND keeps the package source at /app/src/wnba_pipeline. teams.py resolves
# its fallback team-set fixture via Path(__file__).parents[2]/fixtures, which
# only lands on /app/fixtures when the source tree stays in place — a normal
# (non-editable) install would move the package into site-packages and that
# fallback path would no longer resolve.
RUN pip install --no-cache-dir -e .

# The web app is read-only (SELECT against Postgres) — no volume needed. Railway
# rejects the Dockerfile VOLUME instruction, so it is intentionally omitted.

# Default command: serve the site. This mirrors railway.toml's startCommand and
# is the fallback if Railway ever runs the image default. Shell form so $PORT
# expands; ${PORT:-8080} keeps `docker run` working locally without $PORT set.
# The scrapers are NOT started here — they run on GitHub Actions
# (.github/workflows/scrape.yml) and publish to the same Postgres.
CMD gunicorn wnba_pipeline.web:app -b 0.0.0.0:${PORT:-8080} --workers 2 --timeout 60
