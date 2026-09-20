#!/usr/bin/env bash
# Install the hardening layer into a live Hermes install.
#
#   bash scripts/install.sh            # dry run, shows every change
#   bash scripts/install.sh --apply    # actually do it
#
# Everything is reversible: scripts/uninstall.sh restores the backups this
# script takes. Nothing is overwritten without a timestamped .bak.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HOOKS_DST="$HERMES_HOME/agent-hooks"
STATE_DIR="$HERMES_HOME/agent-hooks-state"
CONFIG="$HERMES_HOME/config.yaml"
SOUL="$HERMES_HOME/SOUL.md"
STAMP="$(date +%Y%m%d_%H%M%S)"

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

say()  { echo "  $*"; }
run()  { if [[ $APPLY -eq 1 ]]; then eval "$@"; else echo "    would: $*"; fi; }

echo "Hermes hardening installer"
echo "  repo:        $REPO"
echo "  hermes home: $HERMES_HOME"
[[ $APPLY -eq 1 ]] && echo "  mode:        APPLY" || echo "  mode:        DRY RUN (pass --apply to commit)"
echo

if [[ ! -d "$HERMES_HOME" ]]; then
  echo "ERROR: $HERMES_HOME not found. Is Hermes installed?" >&2
  exit 1
fi

# ---------------------------------------------------------------- preflight
echo "[0/5] Preflight"
if ! command -v python3 >/dev/null; then
  echo "ERROR: python3 not on PATH" >&2; exit 1
fi
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
say "python3 = $PYV (hooks are 3.9-compatible)"

say "running test suite before touching anything..."
if ! python3 "$REPO/tests/test_classifier.py" >/dev/null 2>&1; then
  echo "ERROR: classifier tests FAIL. Refusing to install." >&2
  echo "       run: python3 tests/test_classifier.py" >&2
  exit 1
fi
if ! bash "$REPO/tests/test_hooks.sh" >/dev/null 2>&1; then
  echo "ERROR: hook tests FAIL. Refusing to install." >&2
  echo "       run: bash tests/test_hooks.sh" >&2
  exit 1
fi
say "all tests pass"
echo

# ------------------------------------------------------------------ hooks
echo "[1/5] Hook scripts -> $HOOKS_DST"
run "mkdir -p '$HOOKS_DST' '$STATE_DIR'"
for f in "$REPO"/hooks/*.py; do
  run "cp '$f' '$HOOKS_DST/'"
done
run "chmod +x '$HOOKS_DST'/*.py"
say "installed $(ls -1 "$REPO"/hooks/*.py | wc -l | tr -d ' ') scripts"

# cp refreshes mtime even when content is identical, and Hermes records the
# mtime at approval time. Without revoking, a re-install leaves every hook
# flagged "script modified since approval" -- and a hook in that state does
# NOT fire. Revoke here so the consent step below re-approves cleanly.
if [[ -f "$HOME/.hermes/shell-hooks-allowlist.json" ]]; then
  for f in "$REPO"/hooks/*.py; do
    base="$(basename "$f")"
    [[ "$base" == "lib_classify.py" ]] && continue
    run "hermes hooks revoke '~/.hermes/agent-hooks/$base' >/dev/null 2>&1 || true"
  done
  say "revoked stale approvals (mtime changed on copy)"
fi
echo

# ------------------------------------------------------------- skill index
echo "[2/5] Skill index"
run "python3 '$REPO/scripts/build_skill_index.py'"
say "re-run that command whenever you add or edit skills"
echo

# -------------------------------------------------------------------- soul
echo "[3/5] SOUL.md"
if [[ -f "$SOUL" ]]; then
  run "cp '$SOUL' '$SOUL.bak.$STAMP'"
  say "backed up existing SOUL.md -> SOUL.md.bak.$STAMP"
fi
run "cp '$REPO/soul/SOUL.md' '$SOUL'"
say "installed operating-discipline SOUL.md"
echo

# ------------------------------------------------------------------ config
echo "[4/5] config.yaml hooks block"
# A Hermes install normally has this, but a fresh HERMES_HOME may not. Without
# it the merge exits 1 and `set -e` aborts the install half-done: SOUL.md
# written, consent never granted, config flips never applied.
[[ -f "$CONFIG" ]] || run "touch '$CONFIG'"
if [[ -f "$CONFIG" ]]; then
  run "cp '$CONFIG' '$CONFIG.bak.$STAMP'"
  say "backed up config.yaml -> config.yaml.bak.$STAMP"
fi
if grep -qE '^hooks:' "$CONFIG" 2>/dev/null; then
  if grep -q 'agent-hooks/question_gate_pre_llm.py' "$CONFIG" 2>/dev/null; then
    say "hooks block already installed by this tool - leaving as is"
    say "(edit config/hooks.yaml then re-run to change it)"
  else
    say "WARNING: config.yaml has a 'hooks:' key this tool did not write."
    say "         Merge config/hooks.yaml by hand to avoid clobbering it."
  fi
else
  run "python3 '$REPO/scripts/merge_hooks_config.py' '$CONFIG' '$REPO/config/hooks.yaml'"
  say "appended hooks block"
fi
echo

# ----------------------------------------------------------- config flips
echo "[5/5] Config flips"
say "these reduce blast radius; each is independently reversible"
run "hermes config set agent.verify_on_stop true"
run "hermes config set tool_loop_guardrails.hard_stop_enabled true"
run "hermes config set delegation.max_concurrent_children 4"
echo

echo "Done."
if [[ $APPLY -eq 1 ]]; then
  cat <<'EOF'

NEXT:
  1. Grant hook consent (required - hooks do NOT fire until approved):
       hermes --accept-hooks -z "ok"
  2. Verify:
       hermes hooks doctor      # must say "All shell hooks look healthy"
  3. Restart Hermes, the desktop app, and the gateway. Hooks register at
     process start, so anything already running will not have them.
  4. Smoke test: ask "what did you do so far" and confirm nothing is edited.

ROLLBACK:  bash scripts/uninstall.sh --apply
EOF
else
  echo "Dry run only. Re-run with --apply to commit."
fi
