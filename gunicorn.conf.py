"""Gunicorn configuration for the offdutylocks.com web service.

The bind port is read from the environment **in Python**, not from a ``$PORT``
token in the start command. Railway executes the start command without shell
variable interpolation, so a literal ``$PORT`` on the gunicorn command line
never expands — gunicorn then rejects it with::

    Error: '$PORT' is not a valid port number.

Reading ``os.environ`` here happens at runtime and is shell-independent, so the
bind is always a valid ``host:port`` whether or not Railway injects ``PORT``
(falling back to 8080, Railway's default target port).

The bind *host* is ``[::]`` rather than ``0.0.0.0`` wherever the platform
supports it. Railway's private network is IPv6-only, so a listener on
``0.0.0.0`` is unreachable over it; only the IPv4 healthcheck probe (which
arrives from the 100.64.0.0/10 range) can see it. That combination produces the
confusing state where "Healthcheck succeeded!" appears in the deploy logs while
the public domain still answers 502 with ``x-railway-fallback: true``.

``[::]`` is not blindly assumed, because gunicorn creates the ``AF_INET6``
socket itself and never sets ``IPV6_V6ONLY`` (see ``gunicorn.sock.BaseSocket``).
The listener is therefore dual-stack — accepting IPv4-mapped connections too —
only when the kernel's ``net.ipv6.bindv6only`` default is 0, as it is on Linux.
Binding ``[::]`` on a host where that default is 1 would serve IPv6 *and drop
the IPv4 healthcheck that currently passes*, so the socket is probed at startup
and the bind degrades safely:

* no usable IPv6 at all (``AF_INET6`` unavailable, or ``::`` not bindable —
  e.g. an IPv4-only CI container) -> ``0.0.0.0:PORT``
* IPv6 with ``bindv6only=1`` -> both ``0.0.0.0:PORT`` and ``[::]:PORT``
* IPv6 with ``bindv6only=0`` (Linux default, Railway) -> ``[::]:PORT`` alone,
  which serves IPv4 and IPv6 on one socket
"""

import os
import socket


def _ipv6_bind_mode():
    """Return True for dual-stack ``[::]``, False for IPv6-only, None for no IPv6.

    The probe binds an ephemeral port rather than only creating the socket:
    some environments allow an ``AF_INET6`` socket to be constructed but reject
    the actual ``::`` bind with ``EAFNOSUPPORT``, and only a real bind
    distinguishes the two.
    """
    try:
        probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return None
    with probe:
        try:
            # Read (never set) IPV6_V6ONLY: this is the kernel default that
            # gunicorn's own socket will inherit.
            v6only = probe.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
            probe.bind(("::", 0))
        except OSError:
            return None
    return v6only == 0


def _resolve_bind(port):
    dual_stack = _ipv6_bind_mode()
    if dual_stack is None:
        return "0.0.0.0:%s" % port
    if dual_stack:
        return "[::]:%s" % port
    # IPv6-only listener: pair it with an explicit IPv4 one so the Railway
    # healthcheck probe still has something to connect to.
    return ["0.0.0.0:%s" % port, "[::]:%s" % port]


# Bind to Railway's injected PORT, or 8080 if it is not set.
bind = _resolve_bind(os.environ.get("PORT", "8080"))

# Worker count: honor the conventional WEB_CONCURRENCY, default 2.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))

# Give slow first queries headroom; the app itself is read-only.
timeout = 60

# Unbuffered-friendly access/err logging to stdout/stderr for Railway logs.
accesslog = "-"
errorlog = "-"
