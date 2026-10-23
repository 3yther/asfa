# Security

## This repository is public

`3yther/asfa` is a **public** repo. Anything committed is world-readable from
that moment, and deleting it later does not help: the old blob stays in git
history and on any clone or fork. So the rule is:

> A secret that reaches a commit must be **rotated**, not just deleted.

Nothing real belongs in a tracked file. Secrets live in environment variables —
see `.env.example` for the full list, and the Critical Rules in `CLAUDE.md`.
`.env` is gitignored and must stay that way.

## Secret scanning

This project uses [`gitleaks`](https://github.com/gitleaks/gitleaks) to stop
secrets (API keys, tokens, passwords) reaching a commit.

### Setup — one time, after cloning

```bash
# 1. install gitleaks
brew install gitleaks                 # macOS
# Linux: grab a release binary from
#   https://github.com/gitleaks/gitleaks/releases
# or, with a Go toolchain:
#   go install github.com/zricethezav/gitleaks/v8@latest

# 2. point git at the tracked hooks directory
git config core.hooksPath .githooks
```

That second command is the whole install. `core.hooksPath` makes git run
`.githooks/pre-commit` directly, so the hook is version-controlled and travels
with the repo.

**Do not** copy the hook into `.git/hooks/` instead. That was the old
instruction here and it failed in a way that is hard to notice: the copy is
untracked, so a fresh clone silently has no hook at all, and an edit to the
tracked file never reaches the copy. For a long stretch this repo documented a
hook whose source file did not exist, while `.git/hooks/` sat empty — nothing
was scanning anything.

### Check the install worked

```bash
git config --get core.hooksPath      # must print: .githooks
ls -l .githooks/pre-commit           # must be executable
```

If `core.hooksPath` prints nothing, the hook is **not** active.

### What the hook does

- Scans the **staged diff** on every `git commit`.
- Fails the commit if gitleaks reports a finding, with the value redacted.
- **Fails closed when gitleaks is not installed.** A secret-scanning hook that
  passes silently when its scanner is missing is worse than no hook, because it
  is trusted. Install gitleaks, or pass `--no-verify` knowingly.

### Scan the whole history

The hook only looks at what you are committing. To scan everything:

```bash
gitleaks detect --redact --verbose
```

Expect this to be slower, and to flag things already dealt with — see
`.gitleaksignore`.

### False positives

In order of preference:

1. **Reword the line.** A placeholder that reads as one (`"$MY_TOKEN"`, not a
   realistic-looking literal) is better advice anyway, and a literal token on a
   command line ends up in shell history.
2. **Ignore the specific finding.** Add its fingerprint to `.gitleaksignore`
   with a comment explaining why it is safe. Get fingerprints from:
   ```bash
   gitleaks git --staged --report-format json --report-path -
   ```

Never add a blanket rule or disable a gitleaks rule outright.

## If a secret was committed

1. **Rotate it first.** Revoke the old value at the provider. This is the step
   that actually fixes the exposure; everything else is cleanup.
2. Remove the literal from the working tree and replace it with an env var or a
   substituted placeholder.
3. Decide whether to purge history (`git filter-repo`, or BFG). A purge
   force-pushes and rewrites every downstream clone, so it is a deliberate
   choice, not a reflex — and it is pointless unless step 1 is done.

### Known past exposure

`grafana/01-grafana-reader.sql` carried a literal Postgres password for the
read-only `grafana_reader` role, committed in `2e39b09` and public from then on.
The literal has been removed and the file now takes a
`:GRAFANA_READER_PASSWORD` placeholder substituted at paste time; the old value
is being rotated out of use separately, since removal alone does not undo the
exposure. That value stays in git history and must never be reinstated. The
role was `SELECT`-only on 13 non-sensitive tables, which bounded the blast
radius but does not change the rule above.
