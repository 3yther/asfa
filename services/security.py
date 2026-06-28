"""Security hardening helpers for ASFA.

Currently this houses the login/brute-force rate limiter. Storage is in-process
memory (``memory://``), which is correct for our single-gunicorn-worker setup —
all requests hit the same process so the counters are shared. If we ever scale
to multiple workers/instances, swap ``storage_uri`` for a shared backend (Redis).
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def init_rate_limiter(app):
    """Initialise and attach a rate limiter to the Flask app.

    Default limits apply app-wide as a coarse abuse guard; the login route gets
    a tighter, explicit ``@limiter.limit`` decorator in ``app.py``.
    """
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",
    )
    return limiter
