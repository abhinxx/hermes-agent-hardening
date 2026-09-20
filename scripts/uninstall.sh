#!/usr/bin/env bash
# Remove the hardening layer and restore the most recent backups.
#
#   bash scripts/uninstall.sh            # dry run
#   bash scripts/uninstall.sh --apply

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HOOKS_DST="$HERMES_HOME/agent-hooks"
STATE_DIR="$HERMES_HOME/agent-hooks-state"
CONFIG="$HERMES_HOME/config.yaml"
SOUL="$HERMES_HOME/SOUL.md"

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

run() { if [[ $APPLY -eq 1 ]]; then eval "$@"; else echo "    would: $*"; fi; }

echo "Hermes hardening uninstaller"
[[ $APPLY -eq 1 ]] && echo "  mode: APPLY" || echo "  mode: DRY RUN"
echo

latest_backup() {
  ls -1t "$1".bak.* 2>/dev/null | head -1 || true
}

echo "[1/4] Restore config.yaml"
CB="$(latest_backup "$CONFIG")"
if [[ -n "$CB" ]]; then
  run "cp '$CB' '$CONFIG'"
  echo "    from $CB"
else
  echo "    no backup found - remove the 'hooks:' block by hand"
fi

echo "[2/4] Restore SOUL.md"
SB="$(latest_backup "$SOUL")"
if [[ -n "$SB" ]]; then
  run "cp '$SB' '$SOUL'"
  echo "    from $SB"
else
  echo "    no backup found - leaving SOUL.md in place"
fi

echo "[3/6] Remove hook scripts"
run "rm -rf '$HOOKS_DST'"

echo "[4/6] Remove the footer plugin"
run "rm -rf '$HERMES_HOME/plugins/hermes-autogit'"
echo "    (also run: hermes plugins disable hermes-autogit)"

echo "[5/6] Remove the undo CLI"
run "rm -f '$HOME/.local/bin/hermes-autogit-undo'"

echo "[6/6] Remove hook state (locks, turn verdicts, skill index, receipts)"
run "rm -rf '$STATE_DIR'"

echo
echo "Config flips are NOT reverted automatically. To undo them:"
echo "  hermes config set agent.verify_on_stop false"
echo "  hermes config set tool_loop_guardrails.hard_stop_enabled false"
echo "  hermes config set delegation.max_concurrent_children 10"
echo
echo "Your project folders and their git repos are left alone on purpose."
echo "Subagent output under ~/hermes_runs/ is left alone on purpose."
[[ $APPLY -eq 1 ]] || echo
[[ $APPLY -eq 1 ]] || echo "Dry run only. Re-run with --apply."
