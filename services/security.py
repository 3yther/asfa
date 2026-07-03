"""Security hardening helpers for ASFA.

Houses the rate limiter. Storage is in-process memory (``memory://``), which is
correct for our single-gunicorn-worker setup — all requests hit the same process
so the counters are shared. If we ever scale to multiple workers/instances, swap
``storage_uri`` for a shared backend (Redis).

Two tiers, chosen per request (see ``_authenticated_read``):

* **Authenticated dashboard reads** — safe-method (GET/HEAD) requests from a
  logged-in session get a *generous* budget. ``command.html`` fans out ~20 card
  reads on a single load and more when tab-hopping; this is the owner polling
  their own dashboard, not an attack surface, so a tight budget just 429s normal
  use. The budget is still finite as a backstop against a runaway client.
* **Everything else** — anonymous requests AND all state-changing writes — keeps
  the strict budget, which is where brute-force / abuse protection matters.

Login is *additionally* protected by its explicit ``5 per minute`` limit and the
DB-backed ``auth_failures`` lockout in ``app.py`` (both unchanged by this split).
Authenticated ``/api/gym/*`` is fully exempt via a ``request_filter`` in
``app.py`` (a logged workout fires 30+ set writes) — that stays as-is.
"""

from flask import request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Generous budget for the owner's own authenticated reads.
_AUTHED_READ_BURST = "120 per minute"
_AUTHED_READ_SUSTAINED = "1200 per hour"

# Strict budget for anonymous traffic and every write (unchanged from the
# original app-wide default).
_STRICT_HOURLY = "50 per hour"
_STRICT_DAILY = "200 per day"


def _authenticated_read() -> bool:
    """True only for a safe-method request carried by a logged-in session.

    Guarded so it's safe to evaluate outside a request context (falls back to
    the strict tier, never the generous one)."""
    try:
        return request.method in _SAFE_METHODS and bool(session.get("authed"))
    except Exception:
        return False


# flask-limiter evaluates each default-limit callable per request, and keys the
# counter by the returned limit string — so the generous and strict tiers use
# separate buckets and an anonymous caller can never reach the generous one.
def _burst_limit() -> str:
    return _AUTHED_READ_BURST if _authenticated_read() else _STRICT_HOURLY


def _sustained_limit() -> str:
    return _AUTHED_READ_SUSTAINED if _authenticated_read() else _STRICT_DAILY


def init_rate_limiter(app):
    """Initialise and attach the tiered rate limiter to the Flask app.

    The login route overrides these defaults with its own ``@limiter.limit``.
    """
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[_burst_limit, _sustained_limit],
        storage_uri="memory://",
    )
    return limiter
