#!/usr/bin/env bash
# One-command setup on a fresh machine that already has Hermes.
#
#   curl -fsSL https://raw.githubusercontent.com/abhinxx/hermes-agent-hardening/main/bootstrap.sh | bash
#
# Clones to ~/.hermes/hardening (or updates it), runs the installer, and tells
# you the two manual steps. Safe to re-run.
#
# Non-interactive (CI, containers):
#   curl -fsSL .../bootstrap.sh | HERMES_CODES_ROOT=~/work bash

set -euo pipefail

REPO_URL="https://github.com/abhinxx/hermes-agent-hardening"
DEST="${HERMES_HARDENING_DIR:-$HOME/.hermes/hardening}"

echo "hermes-agent-hardening bootstrap"
echo

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

if [[ -d "$DEST/.git" ]]; then
  echo "updating $DEST"
  git -C "$DEST" pull --ff-only -q || echo "  (pull skipped - local changes)"
else
  echo "cloning into $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone -q "$REPO_URL" "$DEST"
fi

echo
# Pass through a preset root so piped/CI runs never block on the prompt.
SETUP_ARGS=""
if [[ -n "${HERMES_CODES_ROOT:-}" ]]; then
  SETUP_ARGS="--root $HERMES_CODES_ROOT"
elif [[ ! -t 0 ]]; then
  SETUP_ARGS="--yes"      # piped into bash: take the detected default
fi
export SETUP_ARGS

bash "$DEST/scripts/install.sh" --apply

cat <<'EOF'

TWO MANUAL STEPS REMAIN:

  1. Grant hook consent. Hooks do NOT fire until approved:

       hermes --accept-hooks -z "ok"
       hermes hooks doctor          # must say "All shell hooks look healthy"

  2. Restart Hermes, the desktop app, and the gateway.
     Hooks and plugins register at process start.

Then check it works:

       hermes -z "make a file called hello.md in a project called demo"

  You should see the file land in <projects-root>/demo/ and the reply end with
  a line like:  [git] demo @ a3f9c21 · commit #1 · local only

EOF
