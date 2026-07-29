"""Gunicorn configuration for the offdutylocks.com web service.

The bind port is read from the environment **in Python**, not from a ``$PORT``
token in the start command. Railway executes the start command without shell
variable interpolation, so a literal ``$PORT`` on the gunicorn command line
never expands — gunicorn then rejects it with::

    Error: '$PORT' is not a valid port number.

Reading ``os.environ`` here happens at runtime and is shell-independent, so the
bind is always a valid ``host:port`` whether or not Railway injects ``PORT``
(falling back to 8080, Railway's default target port).
"""

import os

# Bind to Railway's injected PORT, or 8080 if it is not set.
bind = "0.0.0.0:%s" % os.environ.get("PORT", "8080")

# Worker count: honor the conventional WEB_CONCURRENCY, default 2.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))

# Give slow first queries headroom; the app itself is read-only.
timeout = 60

# Unbuffered-friendly access/err logging to stdout/stderr for Railway logs.
accesslog = "-"
errorlog = "-"
