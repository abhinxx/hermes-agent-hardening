#!/usr/bin/env bash
# End-to-end test of every hook against synthetic stdin payloads matching the
# documented Hermes wire protocol. No Hermes install required.
#
#   bash tests/test_hooks.sh
#
# Exit 0 = all pass.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="$HERE/../hooks"
STATE="$HOME/.hermes/agent-hooks-state"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok()   { echo "  ok   - $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL - $1"; FAIL=$((FAIL+1)); }

check_exit() { # desc expected actual
  if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1 (expected exit $2, got $3)"; fi
}

echo "== question-gate: pre_llm_call arms state on a question =="
mkdir -p "$STATE"
rm -f "$STATE/testsess.turn.json"
echo '{"hook_event_name":"pre_llm_call","session_id":"testsess","extra":{"user_message":"what did you do so far"}}' \
  | python3 "$HOOKS/question_gate_pre_llm.py" >"$TMP/out1" 2>/dev/null
check_exit "pre_llm exits 0" 0 $?
if grep -q "ANSWER-ONLY" "$TMP/out1"; then ok "injects reminder"; else bad "no reminder injected"; fi
if [[ -f "$STATE/testsess.turn.json" ]] && grep -q '"answer_only": true' "$STATE/testsess.turn.json"; then
  ok "state armed"; else bad "state not armed"; fi

echo "== question-gate: pre_tool_call BLOCKS write_file on armed turn =="
echo '{"hook_event_name":"pre_tool_call","tool_name":"write_file","tool_input":{"path":"/tmp/x"},"session_id":"testsess"}' \
  | python3 "$HOOKS/question_gate_pre_tool.py" >/dev/null 2>"$TMP/err2"
check_exit "blocks with exit 2" 2 $?
if grep -q "ANSWER-ONLY" "$TMP/err2"; then ok "block reason on stderr"; else bad "no reason"; fi

echo "== question-gate: read-only tools stay ALLOWED on armed turn =="
echo '{"hook_event_name":"pre_tool_call","tool_name":"read_file","tool_input":{"path":"/tmp/x"},"session_id":"testsess"}' \
  | python3 "$HOOKS/question_gate_pre_tool.py" >/dev/null 2>&1
check_exit "read_file allowed" 0 $?

echo "== question-gate: work order does NOT arm =="
rm -f "$STATE/testsess2.turn.json"
echo '{"hook_event_name":"pre_llm_call","session_id":"testsess2","extra":{"user_message":"make it light mode"}}' \
  | python3 "$HOOKS/question_gate_pre_llm.py" >/dev/null 2>&1
if grep -q '"answer_only": false' "$STATE/testsess2.turn.json"; then ok "work order not armed"; else bad "work order wrongly armed"; fi
echo '{"hook_event_name":"pre_tool_call","tool_name":"write_file","tool_input":{"path":"/tmp/x"},"session_id":"testsess2"}' \
  | python3 "$HOOKS/question_gate_pre_tool.py" >/dev/null 2>&1
check_exit "write_file allowed after work order" 0 $?

echo "== question-gate: GO: override defeats the gate =="
rm -f "$STATE/testsess3.turn.json"
echo '{"hook_event_name":"pre_llm_call","session_id":"testsess3","extra":{"user_message":"why is this broken? GO: fix it"}}' \
  | python3 "$HOOKS/question_gate_pre_llm.py" >/dev/null 2>&1
if grep -q '"answer_only": false' "$STATE/testsess3.turn.json"; then ok "GO: override honoured"; else bad "GO: override ignored"; fi

echo "== question-gate: no state file = fail open =="
echo '{"hook_event_name":"pre_tool_call","tool_name":"write_file","tool_input":{"path":"/tmp/x"},"session_id":"nostate-xyz"}' \
  | python3 "$HOOKS/question_gate_pre_tool.py" >/dev/null 2>&1
check_exit "unknown session allowed" 0 $?

echo "== no-full-rewrite: blocks large existing file =="
BIG="$TMP/big.html"; python3 -c "open('$BIG','w').write('x'*40000)"
python3 -c "
import json;print(json.dumps({'hook_event_name':'pre_tool_call','tool_name':'write_file','tool_input':{'path':'$BIG','content':'y'*40000}}))" \
  | python3 "$HOOKS/no_full_rewrite.py" >/dev/null 2>"$TMP/err3"
check_exit "blocks 40KB rewrite" 2 $?
if grep -q "patch" "$TMP/err3"; then ok "suggests patch"; else bad "no suggestion"; fi

echo "== no-full-rewrite: allows new file =="
python3 -c "
import json;print(json.dumps({'hook_event_name':'pre_tool_call','tool_name':'write_file','tool_input':{'path':'$TMP/brand-new.txt','content':'hello'}}))" \
  | python3 "$HOOKS/no_full_rewrite.py" >/dev/null 2>&1
check_exit "new file allowed" 0 $?

echo "== no-full-rewrite: allows small existing file =="
SMALL="$TMP/small.txt"; echo "tiny" > "$SMALL"
python3 -c "
import json;print(json.dumps({'hook_event_name':'pre_tool_call','tool_name':'write_file','tool_input':{'path':'$SMALL','content':'still tiny'}}))" \
  | python3 "$HOOKS/no_full_rewrite.py" >/dev/null 2>&1
check_exit "small file allowed" 0 $?

echo "== browser-lock: first claimant wins, second is blocked =="
rm -f "$STATE"/browser.*.lock
echo '{"hook_event_name":"pre_tool_call","tool_name":"browser_exec","tool_input":{},"session_id":"agent-A"}' \
  | python3 "$HOOKS/browser_lock.py" >/dev/null 2>&1
check_exit "agent-A takes lock" 0 $?
echo '{"hook_event_name":"pre_tool_call","tool_name":"browser_exec","tool_input":{},"session_id":"agent-B"}' \
  | python3 "$HOOKS/browser_lock.py" >/dev/null 2>"$TMP/err4"
check_exit "agent-B blocked" 2 $?
if grep -q "agent-A" "$TMP/err4"; then ok "names the lock owner"; else bad "owner not named"; fi

echo "== browser-lock: distinct named sessions do not collide =="
echo '{"hook_event_name":"pre_tool_call","tool_name":"browser_exec","tool_input":{"session":"other"},"session_id":"agent-B"}' \
  | python3 "$HOOKS/browser_lock.py" >/dev/null 2>&1
check_exit "named session allowed" 0 $?

echo "== browser-lock: same agent can re-enter =="
echo '{"hook_event_name":"pre_tool_call","tool_name":"browser_exec","tool_input":{},"session_id":"agent-A"}' \
  | python3 "$HOOKS/browser_lock.py" >/dev/null 2>&1
check_exit "owner re-enters" 0 $?

echo "== persist-subagent: writes durable output =="
RUNS="$TMP/runs"
echo '{"hook_event_name":"subagent_stop","session_id":"parent-1","extra":{"child_session_id":"child-9","child_role":"leaf","child_status":"completed","child_summary":"Found 12 modules.","duration_ms":4321,"tool_call_history":[{"tool":"web_search"}]}}' \
  | HERMES_RUNS_DIR="$RUNS" python3 "$HOOKS/persist_subagent.py" >/dev/null 2>&1
check_exit "hook exits 0" 0 $?
if [[ -f "$RUNS/parent-1/subagents.jsonl" ]]; then ok "jsonl written"; else bad "no jsonl"; fi
if grep -q "Found 12 modules" "$RUNS/parent-1/child-9.md" 2>/dev/null; then ok "markdown written"; else bad "no markdown"; fi

rm -f "$STATE"/testsess*.turn.json "$STATE"/browser.*.lock
echo
echo "passed: $PASS   failed: $FAIL"
[[ $FAIL -eq 0 ]] || exit 1
