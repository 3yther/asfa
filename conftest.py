"""Root pytest configuration — the one place that owns test database isolation.

Why this file exists
────────────────────
Every test module used to configure its own database at import time:

    _TMP_DB = os.path.join(tempfile.mkdtemp(...), "test.db")
    os.environ["ASFA_DB_PATH"] = _TMP_DB      # module level, before the import
    import database as db

That can only ever work for whichever module Python imports first. `database.py`
reads ASFA_DB_PATH exactly once, at import, into the module global SQLITE_PATH.
By the time the second test module runs its assignment `database` is already in
sys.modules, so the write is inert and that module silently shares the first
module's file. The same applies to APP_PASSWORD, which `app.py` captures into a
module global.

The result was a suite that passed file-by-file and failed as a whole — 232
tests, 3 failures and 7 errors on a full run, 0 in isolation — because meals,
gym sets and a passphrase leaked across module boundaries in collection order.

What this file does instead
───────────────────────────
1. Pins the canonical test environment *before* any test module is imported, and
   imports `database` and `app` here, once. The per-module env writes still run;
   they are simply inert now. No test file needs editing.
2. Hands every test module its own SQLite file (`_isolate_module_db`).
   `get_db()` looks up `database.SQLITE_PATH` at call time, so re-pointing that
   global is all it takes to swap databases mid-session.
3. Rebuilds the schema in each fresh file: re-runs the eager `init_*` functions
   and clears the `_*_READY` latches that make the lazy `_ensure_*_table()`
   helpers no-ops after their first call. Without step 3 a swapped-in database
   would have no tables.
4. Re-syncs `app.APP_PASSWORD` from the environment, so `test_api_keys.py` —
   the only module that actually POSTs /login — can set its own passphrase.

Adding a new test module requires nothing here. It may keep its own
ASFA_DB_PATH/APP_PASSWORD preamble (harmless) or drop it entirely.
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Canonical test environment ───────────────────────────────────────────────
# Set before `database`/`app` are imported below — that ordering is the whole
# point of doing this in a root conftest rather than per module.
os.environ["ASFA_BG_STARTED"] = "1"      # no APScheduler, no Telegram bot
os.environ["APP_PASSWORD"] = "test-pass"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.setdefault("ASFA_TZ", "Europe/London")   # pin the canonical day
os.environ.pop("DATABASE_URL", None)     # force SQLite, never a real Postgres
os.environ["ASFA_DB_PATH"] = os.path.join(
    __import__("tempfile").mkdtemp(prefix="asfa_session_"), "session.db")

import database as db      # noqa: E402  — must follow the env pins above
import app as app_module   # noqa: E402  — runs every init_* against the session DB

# Rate limiting would 429 across the many logins/requests a full run makes.
app_module.limiter.enabled = False
app_module.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

# Mirrors the boot sequence in app.py. Re-run per module against its fresh file.
_INIT_STEPS = (
    db.init_db,
    db.init_agents_db,
    db.init_agent_data,
    db.init_gym_data,
    db.init_workout_plan,
    db.init_fragrance_data,
    db.init_scout_pipeline,
    db.init_apprenticeships,
)


def _reset_lazy_table_latches():
    """Clear every `_<NAME>_READY` guard in database.py.

    Roughly 20 `_ensure_*_table()` helpers create their tables on first use and
    then latch a module-level boolean so later calls are free. Discovered by name
    rather than hardcoded, so a newly added latch is picked up automatically.
    """
    for name in dir(db):
        if name.startswith("_") and name.endswith("_READY") \
                and isinstance(getattr(db, name), bool):
            setattr(db, name, False)


@pytest.fixture(scope="module", autouse=True)
def _isolate_module_db(tmp_path_factory, request):
    """Give each test module a private, freshly initialised SQLite database.

    Module-scoped and autouse so it runs ahead of a module's own `setup_module`
    hook (pytest implements those as module-scoped autouse fixtures too, and
    conftest fixtures are ordered first) — modules that seed fixture data in
    `setup_module` therefore seed into their own file, not the previous one's.
    """
    name = request.module.__name__.rsplit(".", 1)[-1]
    db.SQLITE_PATH = str(tmp_path_factory.mktemp(name) / "test.db")

    _reset_lazy_table_latches()
    for step in _INIT_STEPS:
        step()

    # Scout's apprenticeship half is SQLAlchemy-backed (see models.py). Its
    # engine is bound once at import against the session database, and unlike
    # get_db() it will NOT follow a later db.SQLITE_PATH swap — so re-point the
    # URI and rebuild the engine, or ORM tests silently read the wrong file.
    # Re-registering via init_app() raises ("already registered on this Flask
    # app"), so swap the engine inside Flask-SQLAlchemy's own registry instead.
    import sqlalchemy as sa

    from models import db as orm
    app_module.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db.SQLITE_PATH
    engines = orm._app_engines[app_module.app]
    for engine in engines.values():
        engine.dispose()               # drop pooled connections to the old file
    engines[None] = sa.create_engine("sqlite:///" + db.SQLITE_PATH)
    with app_module.app.app_context():
        orm.session.remove()           # scoped per app context, so clear inside
        orm.create_all()

    # app.py captures APP_PASSWORD into a module global at import; a module that
    # sets its own passphrase (test_api_keys.py) needs it re-read here.
    app_module.APP_PASSWORD = os.environ.get("APP_PASSWORD")

    yield
