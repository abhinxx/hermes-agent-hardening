#!/usr/bin/env bash
# End-to-end test of the workspace + auto-git hooks against synthetic stdin
# payloads matching the documented Hermes wire protocol.
#
#   bash tests/test_workspace_hooks.sh
#
# Fully sandboxed: fake HOME, fake codes root, fake state dir. Never touches
# the real ~/.hermes or any real repo.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="$HERE/../hooks"
SB="$(mktemp -d -t hermes-ws-hooks)"
trap 'rm -rf "$SB"' EXIT

export HOME="$SB/home"
export HERMES_CODES_ROOT="$HOME/Documents/codes"
mkdir -p "$HERMES_CODES_ROOT" "$HOME/Desktop" "$HOME/.hermes/agent-hooks-state"
git config --global user.email "t@example.com" 2>/dev/null
git config --global user.name "Test" 2>/dev/null
git config --global init.defaultBranch main 2>/dev/null

PY=/usr/bin/python3
[[ -x "$PY" ]] || PY=python3
PASS=0; FAIL=0
ok()  { echo "  ok   - $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL - $1"; FAIL=$((FAIL+1)); }
exit_is() { if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1 (exit $3, want $2)"; fi; }

guard() { echo "$1" | "$PY" "$HOOKS/workspace_guard.py" >/dev/null 2>"$SB/err"; echo $?; }

echo "== gate: blocks scatter =="
rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"'"$HOME"'/report.html"},"session_id":"s1"}')
exit_is "deliverable loose in HOME blocked" 2 "$rc"
grep -q "project folder" "$SB/err" && ok "names the correct location" || bad "no guidance"

rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"'"$HOME"'/Desktop/x.md"},"session_id":"s1"}')
exit_is "Desktop deliverable blocked" 2 "$rc"

rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"/tmp/out.html"},"session_id":"s1"}')
exit_is "/tmp deliverable blocked" 2 "$rc"

echo "== gate: allows infrastructure and scratch =="
rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"'"$HOME"'/.hermes/config.yaml"},"session_id":"s1"}')
exit_is ".hermes allowed" 0 "$rc"
rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"/tmp/debug.log"},"session_id":"s1"}')
exit_is "/tmp scratch allowed" 0 "$rc"
rc=$(guard '{"tool_name":"terminal","tool_input":{"command":"ls"},"session_id":"s1"}')
exit_is "non-write tool ignored" 0 "$rc"

echo "== gate: AUTO-INIT (the no-write-without-git rule) =="
PROJ="$HERMES_CODES_ROOT/demo"
rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"'"$PROJ"'/index.html"},"session_id":"s1"}')
exit_is "write into new project allowed" 0 "$rc"
[[ -d "$PROJ/.git" ]] && ok "git repo created BEFORE the write" || bad "no repo created"
[[ -f "$PROJ/.gitignore" ]] && ok ".gitignore written" || bad "no .gitignore"

echo "== gate: never re-inits an existing repo =="
BEFORE=$(cd "$PROJ" && git rev-parse --abbrev-ref HEAD 2>/dev/null)
rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"'"$PROJ"'/second.md"},"session_id":"s1"}')
exit_is "second write allowed" 0 "$rc"
AFTER=$(cd "$PROJ" && git rev-parse --abbrev-ref HEAD 2>/dev/null)
[[ "$BEFORE" == "$AFTER" ]] && ok "branch untouched" || bad "branch changed"

echo "== gate: nested paths resolve to the project root =="
rc=$(guard '{"tool_name":"write_file","tool_input":{"path":"'"$PROJ"'/src/deep/a.py"},"session_id":"s1"}')
exit_is "nested write allowed" 0 "$rc"
[[ ! -d "$PROJ/src/.git" ]] && ok "no nested repo created" || bad "nested repo created"

echo "== pre_verify: one commit per turn =="
echo "<html>one</html>" > "$PROJ/index.html"
echo "# two" > "$PROJ/second.md"
mkdir -p "$PROJ/src/deep"; echo "x=1" > "$PROJ/src/deep/a.py"
echo '{"hook_event_name":"pre_verify","session_id":"s1","extra":{"attempt":0,"final_response":"Built the comparison page. It renders.","changed_paths":["'"$PROJ"'/index.html","'"$PROJ"'/second.md","'"$PROJ"'/src/deep/a.py"]}}' \
  | "$PY" "$HOOKS/autogit_verify.py" >"$SB/out" 2>"$SB/err"
exit_is "verify hook exits 0" 0 $?
[[ "$(cat "$SB/out")" == "{}" ]] && ok "returns {} (never extends the turn)" || bad "returned: $(cat "$SB/out")"
N=$(cd "$PROJ" && git rev-list --count HEAD 2>/dev/null || echo 0)
[[ "$N" == "1" ]] && ok "exactly 1 commit for 3 files" || bad "got $N commits"
SUBJ=$(cd "$PROJ" && git log -1 --format=%s)
[[ "$SUBJ" == "Built the comparison page." ]] && ok "subject from agent's own answer" || bad "subject: $SUBJ"
(cd "$PROJ" && git log -1 --format=%B | grep -q "Shipped-by: hermes-autogit") && ok "undo trailer present" || bad "no trailer"

echo "== pre_verify: idempotent on retry =="
echo '{"hook_event_name":"pre_verify","session_id":"s1","extra":{"attempt":1,"changed_paths":["'"$PROJ"'/index.html"]}}' \
  | "$PY" "$HOOKS/autogit_verify.py" >/dev/null 2>&1
N2=$(cd "$PROJ" && git rev-list --count HEAD)
[[ "$N2" == "1" ]] && ok "attempt>0 does not double-commit" || bad "got $N2"

echo "== secrets gate holds the commit =="
printf 'KEY = "sk-ant-api03-Bq7xR2mTvL9pWzYn4KdHsEjA"\n' > "$PROJ/leak.py"
echo '{"hook_event_name":"on_session_end","session_id":"s1","extra":{"interrupted":false}}' \
  | "$PY" "$HOOKS/autogit_session.py" >/dev/null 2>&1
N3=$(cd "$PROJ" && git rev-list --count HEAD)
[[ "$N3" == "1" ]] && ok "secret NOT committed" || bad "secret committed ($N3)"
[[ -f "$PROJ/leak.py" ]] && ok "file kept on disk" || bad "file destroyed"
rm -f "$PROJ/leak.py"

echo "== on_session_end: catches shell-written files =="
echo "built by shell" > "$PROJ/generated.txt"
"$PY" - "$PROJ" <<'PYEOF'
import json, os, sys
d = os.path.expanduser("~/.hermes/agent-hooks-state/touched")
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "s2.txt"), "w").write(sys.argv[1] + "\n")
PYEOF
echo '{"hook_event_name":"on_session_end","session_id":"s2","extra":{"interrupted":true}}' \
  | "$PY" "$HOOKS/autogit_session.py" >/dev/null 2>&1
N4=$(cd "$PROJ" && git rev-list --count HEAD)
[[ "$N4" == "2" ]] && ok "committed on interrupt" || bad "expected 2 commits, got $N4"

echo "== receipts feed the footer =="
"$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.expanduser("$HOOKS"))
PYEOF
R=$("$PY" -c "
import sys, os, json
sys.path.insert(0, '$HOOKS')
import lib_autogit as A
recs = A.read_receipts('s1', consume=False)
print(len(recs))
")
[[ "$R" -ge 1 ]] && ok "receipt written for the footer" || bad "no receipts"

echo
echo "passed: $PASS   failed: $FAIL"
[[ $FAIL -eq 0 ]]
