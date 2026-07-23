# Container image for the WNBA team-statistics extraction pipeline (Railway).
#
# This pipeline is a CLI batch job, not a web server, so Railway's Railpack
# builder can't infer a start command ("No start command detected") and fails
# the build. This Dockerfile makes the build deterministic and supplies the
# start command explicitly.
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

# Team-stats file store (snapshots, LKG, quarantine, manifests) persists under
# /data. Attach a Railway volume at /data if you run team stats in this image;
# the betting feed writes only to Postgres and needs no volume.
VOLUME ["/data"]

# Default command: the betting feed (VSIN + Action Network -> Postgres), which
# is what runs on Railway (see railway.toml's cron + startCommand). Publishing
# requires DATABASE_URL. Team stats run off-datacenter, e.g.:
#   wnba-pipeline run-team-stats --publish --database-url "<postgres public url>"
CMD ["wnba-pipeline", "betting"]
