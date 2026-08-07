# Container image for offdutylocks.com (Railway).
#
# Railway's Railpack builder can't infer a start command for this project
# ("No start command detected"), so this Dockerfile makes the build
# deterministic and supplies the start command explicitly. The image also
# carries the `wnba-pipeline` CLI for ad-hoc use, but the GitHub Actions
# scrapers do NOT run this image — they pip-install the package directly on
# the runner (.github/workflows/scrape.yml → hash-pinned lock, then
# `pip install -e . --no-deps --no-build-isolation`).
FROM python:3.11-slim

# The runner emits the run manifest as a single JSON line on stdout and
# structured logs on stderr; unbuffered output keeps both live in Railway logs.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Hash-pinned dependencies first, in their own layer: requirements.lock is
# compiled by `uv pip compile pyproject.toml --python-version 3.11
# --generate-hashes`, and --require-hashes makes pip reject anything that does
# not match it byte-for-byte — the image no longer re-resolves PyPI at build
# time, and dependency downloads are cached across source-only changes.
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Copy the whole project. fixtures/ MUST be present at runtime: the expected
# WNBA team set is resolved from fixtures/expected_teams/<season>.json (it is
# never hardcoded), and offline/fixture runs read fixtures/sanitized/.
COPY . /app

# Editable install, deliberately. It puts the `wnba-pipeline` console script on
# PATH AND keeps the package source at /app/src/wnba_pipeline. teams.py resolves
# its fallback team-set fixture via Path(__file__).parents[2]/fixtures, which
# only lands on /app/fixtures when the source tree stays in place — a normal
# (non-editable) install would move the package into site-packages and that
# fallback path would no longer resolve. --no-deps because every dependency is
# already installed from the hash-pinned lock above; --no-build-isolation
# because PEP 517 build isolation would download an un-pinned setuptools —
# python:3.11-slim already ships one, so the build uses only what the image
# and the lock provide.
RUN pip install --no-cache-dir -e . --no-deps --no-build-isolation

# The web app is read-only (SELECT against Postgres) — no volume needed. Railway
# rejects the Dockerfile VOLUME instruction, so it is intentionally omitted.

# Declare the port the server listens on. This is documentation for humans AND a
# routing hint for Railway: for a DOCKERFILE-builder service, EXPOSE is the
# static target-port signal the edge uses when the domain has no explicit target
# port and no PORT service variable is set. Without it the edge can end up with
# no port to route to and serves its own 502 with `x-railway-fallback: true`
# even though the container is healthy and its healthcheck passes.
#
# 3000 matches BOTH the default in gunicorn.conf.py and the target port
# configured on the Railway domain — all three must agree. The healthcheck
# auto-detects whatever port is listening and so passes regardless, but the
# domain routes only to its configured target port: listening anywhere else
# gives a "healthcheck succeeded" deploy that still answers 502 with
# `x-railway-fallback: true`. If PORT *is* injected, gunicorn follows PORT.
EXPOSE 3000

# Drop root. Without a USER directive the serving process runs as uid 0 with
# the editable package source writable at /app/src/wnba_pipeline, so any
# file-write primitive becomes code execution on the next restart — as root,
# with DATABASE_URL in the environment. Railway needs no privileged port (3000),
# so nothing here requires uid 0.
#
# Ownership is set on /app rather than left to root: the editable install
# already ran above, and gunicorn only needs to READ the tree.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin odl \
    && chown -R odl:odl /app
USER odl

# Default command: serve the site. This mirrors railway.toml's startCommand and
# is the fallback if Railway ever runs the image default. Exec form (JSON) so
# no shell is required: the bind port comes from os.environ["PORT"] inside
# gunicorn.conf.py (default 3000), not from a shell-expanded "$PORT" on the
# command line — Railway does not interpolate the start command. The scrapers
# are NOT started here — they run on GitHub Actions (.github/workflows/scrape.yml).
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "wnba_pipeline.web:app"]
