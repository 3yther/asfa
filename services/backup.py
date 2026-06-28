"""Automated production-database backup.

Once a day (and on demand) this dumps the entire Railway PostgreSQL database to a
single `.sql` file and pushes it to the **private** repo `3yther/asfa-backups`,
so the data survives a Railway incident, a bad migration, or accidental deletion.

Guarantees:
  * **No-op on local SQLite.** If `DATABASE_URL` isn't set we return a `skipped`
    result immediately — local dev never produces backups.
  * **No new deps.** Only `requests` + `psycopg2` (both already required) + stdlib.
  * **No git binary required.** Dumps are pushed via the GitHub Contents API.
  * **pg_dump preferred, pure-Python fallback.** If `pg_dump` is on PATH and
    succeeds we use its output; otherwise we dump via psycopg2 directly
    (information_schema → CREATE TABLE + batched INSERTs) so the Railway image
    doesn't need postgres client tools.
  * **Never crashes the app.** `run_backup()` wraps everything and returns an
    error dict instead of raising.

Env vars (set in Railway, never in code):
  DATABASE_URL          — presence enables backups (absent → skipped).
  BACKUP_GITHUB_TOKEN   — fine-grained PAT with contents:write on the repo.
  BACKUP_REPO           — "owner/name", e.g. 3yther/asfa-backups.
"""
import base64
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger("asfa.backup")

GITHUB_API = "https://api.github.com"
DUMP_DIR = "dumps"
RETENTION_DAYS = 30
INSERT_BATCH = 500
# asfa_backup_2026-06-28_0300_UTC.sql
_NAME_RE = re.compile(r"asfa_backup_(\d{4}-\d{2}-\d{2})_\d{4}_UTC\.sql$")


# ── value rendering ──────────────────────────────────────────────────────────

def _ident(name) -> str:
    """Quote a Postgres identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def _sql_literal(v) -> str:
    """Render a Python value as a Postgres SQL literal for an INSERT."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(v).hex() + "'"  # bytea hex input format
    if isinstance(v, (dict, list)):
        import json
        return "'" + json.dumps(v).replace("'", "''") + "'"
    return "'" + str(v).replace("'", "''") + "'"


def _alert(message: str):
    """Best-effort failure notification (never raises)."""
    try:
        from services import alerts
        alerts.send_alert(message, kind="alert")
    except Exception:
        logger.warning("backup failure alert could not be sent", exc_info=True)


# ── dumping ──────────────────────────────────────────────────────────────────

def _pg_dump(url: str):
    """Dump via the pg_dump binary; return the SQL as bytes, or None on failure."""
    try:
        proc = subprocess.run(["pg_dump", url], capture_output=True, timeout=600)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        logger.warning("pg_dump exited %s: %s", proc.returncode,
                       proc.stderr.decode("utf-8", "replace")[:300])
    except FileNotFoundError:
        logger.info("pg_dump not on PATH — using pure-python dump")
    except Exception as e:
        logger.warning("pg_dump failed, falling back to python dump: %s", e)
    return None


def _count_tables_rows(url: str):
    """(n_tables, n_rows) across the public schema — best-effort, for reporting."""
    import psycopg2
    try:
        conn = psycopg2.connect(url)
        try:
            cur = conn.cursor()
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE'")
            tables = [r[0] for r in cur.fetchall()]
            total = 0
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {_ident(t)}")
                total += cur.fetchone()[0]
            return len(tables), total
        finally:
            conn.close()
    except Exception as e:
        logger.warning("table/row count failed: %s", e)
        return None, None


def _python_dump(url: str):
    """Pure-Python dump: schema from information_schema + batched INSERTs.
    Returns (dump_bytes, n_tables, n_rows)."""
    import psycopg2
    parts = [
        f"-- ASFA backup (pure-python) {datetime.now(timezone.utc).isoformat()}\n",
        "-- pg_dump unavailable; schema is best-effort, row data is complete.\n\n",
        "BEGIN;\n\n",
    ]
    n_tables = n_rows = 0
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE' "
                    "ORDER BY table_name")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s "
                        "ORDER BY ordinal_position", (t,))
            cols = cur.fetchall()
            colnames = [c[0] for c in cols]
            ddl = ", ".join(f"{_ident(c[0])} {c[1]}" for c in cols)
            collist = ", ".join(_ident(c) for c in colnames)
            parts.append(f"-- table: {t}\n")
            parts.append(f"CREATE TABLE IF NOT EXISTS {_ident(t)} ({ddl});\n")

            # Stream rows and emit multi-row INSERTs, INSERT_BATCH at a time.
            data = conn.cursor(name=f"dump_{t}")  # server-side cursor
            data.itersize = INSERT_BATCH
            data.execute(f"SELECT * FROM {_ident(t)}")
            batch = []
            while True:
                rows = data.fetchmany(INSERT_BATCH)
                if not rows:
                    break
                for row in rows:
                    batch.append("(" + ", ".join(_sql_literal(v) for v in row) + ")")
                    n_rows += 1
                parts.append(f"INSERT INTO {_ident(t)} ({collist}) VALUES\n")
                parts.append(",\n".join(batch) + ";\n")
                batch = []
            data.close()
            parts.append("\n")
            n_tables += 1
        parts.append("COMMIT;\n")
    finally:
        conn.close()
    return "".join(parts).encode("utf-8"), n_tables, n_rows


# ── GitHub Contents API ──────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_get_sha(repo: str, token: str, path: str):
    r = requests.get(f"{GITHUB_API}/repos/{repo}/contents/{path}",
                     headers=_gh_headers(token), timeout=30)
    return r.json().get("sha") if r.status_code == 200 else None


def _gh_put_file(repo: str, token: str, path: str, content_b64: str, message: str):
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    body = {"message": message, "content": content_b64}
    # Filenames are timestamp-unique, but handle a pre-existing file gracefully.
    sha = _gh_get_sha(repo, token, path)
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=_gh_headers(token), json=body, timeout=60)
    if r.status_code in (200, 201):
        return
    raise RuntimeError(f"GitHub PUT {path} failed: {r.status_code} {r.text[:200]}")


def _gh_list_dir(repo: str, token: str, path: str) -> list:
    r = requests.get(f"{GITHUB_API}/repos/{repo}/contents/{path}",
                     headers=_gh_headers(token), timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _gh_delete_file(repo: str, token: str, path: str, sha: str, message: str):
    r = requests.delete(f"{GITHUB_API}/repos/{repo}/contents/{path}",
                        headers=_gh_headers(token),
                        json={"message": message, "sha": sha}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"GitHub DELETE {path} failed: {r.status_code} {r.text[:120]}")


def _prune_old_dumps(repo: str, token: str, keep_days: int = RETENTION_DAYS) -> int:
    """Delete dumps older than keep_days (by date in the filename). Best-effort."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    pruned = 0
    for item in _gh_list_dir(repo, token, DUMP_DIR):
        if item.get("type") != "file":
            continue
        m = _NAME_RE.match(item.get("name", ""))
        if not m:
            continue
        stamp = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if stamp < cutoff:
            _gh_delete_file(repo, token, item["path"], item["sha"],
                            f"prune old backup: {item['name']}")
            pruned += 1
    return pruned


# ── entry point ──────────────────────────────────────────────────────────────

def _run_backup_inner() -> dict:
    url = os.environ.get("DATABASE_URL")
    if not url:
        logger.info("DB backup skipped — no DATABASE_URL (local SQLite).")
        return {"ok": True, "method": "skipped",
                "reason": "local SQLite — no backup needed"}

    repo = os.environ.get("BACKUP_REPO")
    token = os.environ.get("BACKUP_GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError(
            "BACKUP_REPO and BACKUP_GITHUB_TOKEN must be set to push backups")

    # 1) Build the dump (pg_dump preferred, python fallback).
    dump_bytes = _pg_dump(url)
    if dump_bytes is not None:
        method = "pg_dump"
        tables, rows = _count_tables_rows(url)
    else:
        method = "python"
        dump_bytes, tables, rows = _python_dump(url)

    size = len(dump_bytes)
    fname = f"asfa_backup_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')}_UTC.sql"
    logger.info("DB dump via %s: %s — %d bytes, %s tables, %s rows",
                method, fname, size, tables, rows)

    # 2) Push to the private backups repo via the Contents API.
    content_b64 = base64.b64encode(dump_bytes).decode()
    _gh_put_file(repo, token, f"{DUMP_DIR}/{fname}", content_b64,
                 f"backup: {fname}")
    logger.info("DB backup pushed → %s/%s/%s (%d bytes)", repo, DUMP_DIR, fname, size)

    # 3) Light retention — never let pruning failure affect the result.
    try:
        pruned = _prune_old_dumps(repo, token)
        if pruned:
            logger.info("pruned %d dump(s) older than %d days", pruned, RETENTION_DAYS)
    except Exception as e:
        logger.warning("dump pruning failed (non-fatal): %s", e)

    return {"ok": True, "file": fname, "tables": tables, "rows": rows,
            "bytes": size, "method": method}


def run_backup() -> dict:
    """Dump the prod DB and push it to the backups repo. Never raises.

    Returns:
      success: {"ok": True, "file", "tables", "rows", "bytes", "method"}
      skipped: {"ok": True, "method": "skipped", "reason"}
      failure: {"ok": False, "error"}
    """
    try:
        return _run_backup_inner()
    except Exception as e:
        logger.exception("DB backup failed")
        _alert(f"❌ ASFA DB backup failed: {e}")
        return {"ok": False, "error": str(e)}
