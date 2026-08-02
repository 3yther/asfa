"""SQLAlchemy models for Scout's apprenticeship half.

Ported from the standalone Apprenticeship Radar project. See INTEGRATION_NOTES.md
for the full audit — the short version:

  * ASFA's primary data layer is `database.py`: hand-written SQL over raw
    sqlite3/psycopg2, exposed as module functions and imported everywhere as
    `import database as db`. That layer is untouched and still owns every
    existing table.
  * Flask-SQLAlchemy was added specifically so Radar's `Employer` / `Vacancy` /
    `ScanLog` models port across intact. It points at the *same* database — the
    same SQLite file, the same Postgres DATABASE_URL — so there is still exactly
    one database, read two ways.

NAMING — IMPORTANT
    The SQLAlchemy instance below is called `db` *inside this module only*,
    which is what lets Radar's model bodies (`db.Column`, `db.Model`) port
    verbatim. It must NEVER be imported into `app.py` as `db`: that name is
    bound to the `database` module across the entire app, and shadowing it
    reintroduces the `NameError: 'db'` documented in CLAUDE.md. Import it as:

        from models import db as orm

RESERVED NAMES
    No column may be named `query`, `metadata`, or any other SQLAlchemy
    attribute. Radar shipped `ScanLog.query = db.Column(...)`, which shadowed
    `Model.query` and raised `AttributeError: 'Comparator' object has no
    attribute 'order_by'` at runtime. The attribute here is `query_text`, mapped
    onto a DB column still named "query".
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

import database as _asfa_db

db = SQLAlchemy()


def database_uri() -> str:
    """The SQLAlchemy URI for ASFA's existing database.

    Derived from `database.py` rather than re-reading the environment, so the
    ORM can never end up pointed at a different database than the raw layer
    (e.g. when ASFA_DB_PATH redirects SQLite during tests).
    """
    if _asfa_db.USE_POSTGRES:
        url = _asfa_db.DATABASE_URL
        # Railway hands out postgres://, which SQLAlchemy 2 rejects.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    return "sqlite:///" + _asfa_db.SQLITE_PATH


def init_app(app):
    """Bind the ORM to the Flask app and create the apprenticeship tables.

    Only `employers` and `scan_logs` are created here. `scout_jobs` already
    exists and is owned by database.py — its new apprenticeship columns are
    added by `database.init_apprenticeships()`, which MUST run first so
    create_all() sees a fully-formed table and leaves it alone.
    """
    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()


class Employer(db.Model):
    """The watchlist. Only apprenticeships matched to a watched employer alert."""

    __tablename__ = "employers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    # Aliases used in matching (Vodafone/VodafoneThree, JPMorgan/JP Morgan Chase)
    aliases = db.Column(db.Text, default="")  # comma-separated
    sector = db.Column(db.String(80))
    priority = db.Column(db.Integer, default=2)  # 1=high, 2=med, 3=low
    notes = db.Column(db.Text, default="")
    watching = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def alias_list(self):
        base = [self.name]
        if self.aliases:
            base += [a.strip() for a in self.aliases.split(",") if a.strip()]
        return base

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "aliases": self.aliases or "",
            "sector": self.sector or "",
            "priority": self.priority,
            "notes": self.notes or "",
            "watching": bool(self.watching),
        }


class ScoutJob(db.Model):
    """Maps the EXISTING `scout_jobs` table — jobs and apprenticeships both.

    Radar's separate `vacancies` table is deliberately not ported: one listings
    table feeds one dashboard. Columns above the divider are pre-existing and
    written by services/scout.py's raw-SQL path; columns below were added for
    apprenticeships and are nullable so every existing job row stays valid.

    `source` keeps its original meaning — the PROVIDER (`reed`, `google_jobs`,
    `gov_uk`). The job/apprenticeship split lives in `listing_type`.
    """

    __tablename__ = "scout_jobs"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text)
    company = db.Column(db.Text)
    location = db.Column(db.Text)
    salary = db.Column(db.Text)          # apprenticeship wage lands here
    job_type = db.Column(db.Text)        # "part time" | "apprenticeship"
    url = db.Column(db.Text)             # dedup key for jobs
    description = db.Column(db.Text)
    source = db.Column(db.Text)          # provider: reed | google_jobs | gov_uk
    posted_date = db.Column(db.Text)     # raw string, both formats
    found_date = db.Column(db.Text)      # "%Y-%m-%d %H:%M:%S" — acts as first_seen
    is_new = db.Column(db.Integer, default=1)
    applied = db.Column(db.Integer, default=0)

    # ── apprenticeship columns ───────────────────────────────────────────────
    listing_type = db.Column(db.String(20), default="job", index=True)
    external_ref = db.Column(db.String(50), index=True)  # gov.uk VAC… — dedup key
    employer_id = db.Column(db.Integer, db.ForeignKey("employers.id"), index=True)
    employer_name_raw = db.Column(db.String(300))
    level = db.Column(db.Integer)
    training_course = db.Column(db.String(300))
    closing_text = db.Column(db.String(200))
    closing_date = db.Column(db.Date)
    start_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="open")  # open | closed
    last_seen = db.Column(db.DateTime)
    alerted = db.Column(db.Integer, default=0)

    employer = db.relationship("Employer", backref="listings", lazy="joined")

    @property
    def employer_display(self):
        return (self.employer.name if self.employer else None) or \
            self.employer_name_raw or self.company or ""

    @property
    def days_to_close(self):
        """Whole days until close, or None. Negative once the date has passed."""
        if not self.closing_date:
            return None
        return (self.closing_date - datetime.utcnow().date()).days

    def to_dict(self):
        return {
            "id": self.id,
            "listing_type": self.listing_type or "job",
            "external_ref": self.external_ref,
            "title": self.title,
            "company": self.company,
            "employer": self.employer_display,
            "location": self.location,
            "salary": self.salary,
            "url": self.url,
            "source": self.source,
            "level": self.level,
            "training_course": self.training_course,
            "closing_text": self.closing_text,
            "closing_date": self.closing_date.isoformat() if self.closing_date else None,
            "days_to_close": self.days_to_close,
            "status": self.status,
            "posted_date": self.posted_date,
            "found_date": self.found_date,
            "applied": self.applied,
            "is_new": self.is_new,
        }


class ScanLog(db.Model):
    """Audit trail for gov.uk scans — one row per employer alias / keyword."""

    __tablename__ = "scan_logs"

    id = db.Column(db.Integer, primary_key=True)
    ran_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    kind = db.Column(db.String(50))  # employer | keyword
    # NEVER name this attribute `query` — see the module docstring.
    query_text = db.Column("query", db.String(200))
    results_found = db.Column(db.Integer, default=0)
    new_vacancies = db.Column(db.Integer, default=0)
    duration_ms = db.Column(db.Integer, default=0)
    error = db.Column(db.Text)
