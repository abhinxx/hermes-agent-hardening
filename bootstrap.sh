#!/usr/bin/env bash
# One-command setup on a fresh machine that already has Hermes.
#
#   curl -fsSL https://raw.githubusercontent.com/abhinxx/hermes-agent-hardening/main/bootstrap.sh | bash
#
# Clones (or updates) the repo to ~/.hermes/hardening, runs the test suite,
# installs, and grants hook consent. Idempotent: safe to re-run.

set -euo pipefail

REPO_URL="${HARDENING_REPO:-https://github.com/abhinxx/hermes-agent-hardening.git}"
DEST="${HARDENING_DIR:-$HOME/.hermes/hardening}"

echo "Hermes hardening bootstrap"
echo

command -v git >/dev/null    || { echo "ERROR: git not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 not found" >&2; exit 1; }
command -v hermes >/dev/null  || { echo "ERROR: hermes not found on PATH" >&2; exit 1; }

if [[ -d "$DEST/.git" ]]; then
  echo "[1/4] Updating $DEST"
  git -C "$DEST" pull --ff-only --quiet
else
  echo "[1/4] Cloning into $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone --quiet "$REPO_URL" "$DEST"
fi

cd "$DEST"

echo "[2/4] Running tests"
python3 tests/test_classifier.py >/dev/null || { echo "ERROR: classifier tests failed" >&2; exit 1; }
bash tests/test_hooks.sh >/dev/null        || { echo "ERROR: hook tests failed" >&2; exit 1; }
echo "      all pass"

echo "[3/4] Installing"
bash scripts/install.sh --apply >/dev/null
echo "      hooks, SOUL.md, config, skill index installed"

echo "[4/4] Granting hook consent"
hermes --accept-hooks -z "ok" >/dev/null 2>&1 || true
if hermes hooks doctor 2>&1 | grep -q "All shell hooks look healthy"; then
  echo "      all hooks healthy"
else
  echo "      WARNING: run 'hermes hooks doctor' to check"
fi

cat <<EOF

Done. Restart Hermes (and the desktop app / gateway) so the hooks load.

  verify:    hermes hooks doctor
  rollback:  bash $DEST/scripts/uninstall.sh --apply
EOF
