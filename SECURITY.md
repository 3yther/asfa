# Security

## Secret Scanning

This project uses `gitleaks` to prevent accidental commits of secrets (API keys, tokens, passwords).

### Setup (one-time, after cloning)

```bash
brew install gitleaks
cp .githooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

### Testing

```bash
gitleaks detect --verbose  # Scan entire history
```

The pre-commit hook will block any commit containing detected secrets.
