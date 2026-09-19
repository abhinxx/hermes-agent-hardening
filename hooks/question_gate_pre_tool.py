#!/usr/bin/env python3
"""pre_tool_call hook -- block mutating tools on an answer-only turn.

This is the enforcement half of the question-gate. It reads the verdict that
question_gate_pre_llm.py wrote for this session and, if the turn was
classified answer-only by the STRICT tier, blocks mutating tool calls with
exit code 2.

Exit 2 is the Claude-Code / Cursor compatible block signal: Hermes treats it
as a block even when stdout carries no block JSON, using stderr as the reason.

WHY THIS IS SAFE TO ENFORCE
  The strict tier scores 100% precision with ZERO false blocks against 56
  hand-labelled real messages (tests/test_classifier.py). It deliberately
  misses roughly a third of genuine questions rather than risk blocking one
  real instruction.

STALENESS
  A verdict older than TTL_SECONDS is ignored. If the pre_llm_call hook did
  not run (misconfigured, crashed), there is no state file and nothing is
  blocked. Fail-open is correct here: this hook is a discipline gate, not a
  security boundary. Do NOT set fail_closed: true on it.

ESCAPE HATCH
  Put "GO:" or "DO IT:" anywhere in your message and the pre_llm_call
  classifier never arms the gate in the first place.

stdin:  {"hook_event_name": "pre_tool_call", "tool_name": "...",
         "tool_input": {...}, "session_id": "..."}
stdout: {} on allow
exit 2 + stderr message on block
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.hermes/agent-hooks-state")
TTL_SECONDS = 1800

# Tools that change the world. Read-only tools stay available so the agent can
# still look something up in order to answer.
MUTATING_TOOLS = {
    "write_file",
    "patch",
    "terminal",
    "browser_exec",
    "delegate_task",
    "execute_code",
    "image_generate",
    "text_to_speech",
    "cronjob",
    "skill_manage",
    "memory",
}

# Even on an answer-only turn these are fine -- they inform an answer.
ALWAYS_ALLOWED = {
    "read_file",
    "search_files",
    "web_search",
    "web_extract",
    "session_search",
    "skill_view",
    "skills_list",
    "todo",
    "clarify",
    "vision_analyze",
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    tool_name = payload.get("tool_name") or ""
    session_id = payload.get("session_id") or "unknown"

    if tool_name in ALWAYS_ALLOWED or tool_name not in MUTATING_TOOLS:
        print("{}")
        return 0

    state_path = os.path.join(STATE_DIR, f"{session_id}.turn.json")
    if not os.path.exists(state_path):
        print("{}")
        return 0

    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        print("{}")
        return 0

    if time.time() - float(state.get("ts", 0)) > TTL_SECONDS:
        print("{}")
        return 0

    if not state.get("answer_only"):
        print("{}")
        return 0

    reason = state.get("reason", "question detected")
    head = (state.get("message_head") or "").replace("\n", " ")[:90]

    sys.stderr.write(
        f"BLOCKED by question-gate: this turn is ANSWER-ONLY ({reason}).\n"
        f"The user's message was: \"{head}\"\n"
        f"They asked something. Answer it in text first.\n"
        f"If work really is required, say what you would do and wait for a "
        f"go-ahead, or ask the user to re-send with a 'GO:' prefix.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
