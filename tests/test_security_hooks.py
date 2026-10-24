"""The gitleaks pre-commit hook exists, is executable, and fails closed.

This file exists because the previous arrangement rotted silently. SECURITY.md
told you to `cp .githooks/pre-commit .git/hooks/pre-commit`, but
`.githooks/pre-commit` was never committed — so the documented hook could not be
installed, `.git/hooks/` sat empty, and nothing scanned anything. Untracked
state cannot be tested; a tracked hook plus `core.hooksPath` can.

These are static checks on the hook script. They do not run gitleaks (CI need
not have it installed) — they pin the properties that make the hook trustworthy.
"""
import os
import stat
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, ".githooks", "pre-commit")


def _hook_text():
    with open(HOOK, encoding="utf-8") as fh:
        return fh.read()


def test_the_hook_is_tracked_and_present():
    """The failure mode this whole file guards: a documented hook that is not
    actually in the repo."""
    assert os.path.isfile(HOOK), ".githooks/pre-commit is missing"

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".githooks/pre-commit"],
        cwd=REPO, capture_output=True, text=True)
    assert tracked.returncode == 0, ".githooks/pre-commit exists but is untracked"


def test_the_hook_is_executable():
    mode = os.stat(HOOK).st_mode
    assert mode & stat.S_IXUSR, ".githooks/pre-commit is not executable"


def test_the_hook_is_valid_shell():
    r = subprocess.run(["bash", "-n", HOOK], capture_output=True, text=True)
    assert r.returncode == 0, f"syntax error in the hook: {r.stderr}"


def test_the_hook_fails_closed_when_gitleaks_is_absent(tmp_path):
    """A scanning hook that passes when its scanner is missing is worse than no
    hook, because it is trusted. Run it with a PATH that has no gitleaks."""
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if staged.returncode == 0:
        pytest.skip("nothing staged; the hook correctly short-circuits")

    r = subprocess.run([HOOK], cwd=REPO, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode != 0, "hook passed with no gitleaks on PATH"
    assert "gitleaks is not installed" in r.stderr


def test_the_hook_scans_only_staged_changes():
    """Pre-commit scope. A full-history scan on every commit would be slow
    enough that someone would disable the hook."""
    assert "--staged" in _hook_text() or "diff --cached" in _hook_text()


def test_the_hook_redacts_findings():
    """Findings must not print secret values into a terminal or CI log."""
    assert "--redact" in _hook_text()


def test_the_hook_handles_the_gitleaks_cli_variants():
    """The 8.x CLI moved `protect --staged` to `git --staged`; the hook probes
    for what the installed build supports rather than assuming one."""
    text = _hook_text()
    for sub in ("gitleaks git --staged", "gitleaks protect --staged",
                "gitleaks stdin"):
        assert sub in text, f"hook does not handle: {sub}"


# ── The documentation has to match the mechanism ─────────────────────────────

def _security_md():
    with open(os.path.join(REPO, "SECURITY.md"), encoding="utf-8") as fh:
        return fh.read()


def test_security_md_documents_the_hookspath_install():
    assert "core.hooksPath .githooks" in _security_md()


def test_security_md_does_not_tell_you_to_copy_the_hook():
    """The old instruction. A copy under .git/hooks is untracked, so a fresh
    clone silently has none and edits to the tracked file never reach it."""
    text = _security_md()
    assert "cp .githooks/pre-commit .git/hooks/pre-commit" not in text


def test_gitleaksignore_entries_are_all_commented():
    """Every ignore must say why it is safe, or the file becomes a dumping
    ground that quietly disables scanning."""
    path = os.path.join(REPO, ".gitleaksignore")
    if not os.path.exists(path):
        pytest.skip("no .gitleaksignore")
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    fingerprints = [ln for ln in lines if not ln.startswith("#")]
    comments = [ln for ln in lines if ln.startswith("#")]
    if fingerprints:
        assert comments, ".gitleaksignore has entries but no explanation"
    for fp in fingerprints:
        assert fp.count(":") >= 3, f"not a gitleaks fingerprint: {fp}"


# ── The known past exposure stays fixed ──────────────────────────────────────

def test_grafana_sql_has_no_literal_password():
    """Regression guard for the exposure in 2e39b09."""
    import re
    path = os.path.join(REPO, "grafana", "01-grafana-reader.sql")
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()

    create = re.search(r"CREATE USER grafana_reader WITH PASSWORD\s+(\S+?)\s*;",
                       sql, re.I)
    assert create, "CREATE USER grafana_reader line not found"
    value = create.group(1)
    assert not value.startswith("'"), \
        "a literal password is back in grafana/01-grafana-reader.sql"
    assert value.startswith(":"), \
        f"expected a substituted placeholder, got {value!r}"
