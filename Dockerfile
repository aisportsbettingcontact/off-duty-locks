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

# Declare the port the server listens on. This is documentation for humans AND a
# routing hint for Railway: for a DOCKERFILE-builder service, EXPOSE is the
# static target-port signal the edge uses when the domain has no explicit target
# port and no PORT service variable is set. Without it the edge can end up with
# no port to route to and serves its own 502 with `x-railway-fallback: true`
# even though the container is healthy and its healthcheck passes.
#
# 8080 matches the default in gunicorn.conf.py, so the container listens here
# whether or not Railway injects PORT. If PORT *is* injected with a different
# value, gunicorn follows PORT and Railway routes to that value instead — this
# EXPOSE only supplies the fallback hint.
EXPOSE 8080

# Default command: serve the site. This mirrors railway.toml's startCommand and
# is the fallback if Railway ever runs the image default. Exec form (JSON) so
# no shell is required: the bind port comes from os.environ["PORT"] inside
# gunicorn.conf.py (default 8080), not from a shell-expanded "$PORT" on the
# command line — Railway does not interpolate the start command. The scrapers
# are NOT started here — they run on GitHub Actions (.github/workflows/scrape.yml).
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "wnba_pipeline.web:app"]
