#!/usr/bin/env python3
"""pre_llm_call hook -- classify the incoming turn and record it.

Fires once per turn, BEFORE the model sees the message. Two effects:

  1. Writes the verdict to a per-session state file that the pre_tool_call
     gate reads. This is the only way a tool-blocking hook can know anything
     about what the user actually said -- pre_tool_call receives tool_name and
     tool_input, never the user message.

  2. On an answer-only turn, injects a short reminder into the turn via the
     {"context": "..."} response shape.

Fail-open by design: any error here must not stop the agent. The state file is
written first so a crash in the injection path still arms the gate.

stdin:  {"hook_event_name": "pre_llm_call", "session_id": "...",
         "extra": {"user_message": "..."}}
stdout: {} | {"context": "..."}
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser("~/.hermes/agent-hooks-state")

REMINDER = (
    "TURN MODE: ANSWER-ONLY. The user asked a question or told you to stop. "
    "Reply in text. Do not call write_file, patch, terminal, browser_exec, or "
    "delegate_task this turn - a policy hook will block them. Read-only tools "
    "are allowed if you genuinely need them to answer. If you believe work is "
    "required, say what you would do and ask for a go-ahead."
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    session_id = payload.get("session_id") or "unknown"
    extra = payload.get("extra") or {}
    user_message = extra.get("user_message") or ""

    if not user_message:
        print("{}")
        return 0

    try:
        from lib_classify import classify

        strict = classify(user_message, tier="strict")
        advise = classify(user_message, tier="advise")
    except Exception as exc:
        # Classifier broken: arm nothing, block nothing.
        print(json.dumps({}))
        sys.stderr.write(f"question-gate: classifier failed: {exc}\n")
        return 0

    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{session_id}.turn.json")

    state = {
        "ts": time.time(),
        "session_id": session_id,
        "answer_only": strict.block,      # only the STRICT tier may block
        "advisory": advise.block,         # advise tier only warns
        "reason": strict.reason,
        "matched": strict.matched,
        "message_head": user_message[:160],
    }

    try:
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, state_path)
    except Exception as exc:
        sys.stderr.write(f"question-gate: state write failed: {exc}\n")

    if strict.block:
        print(json.dumps({"context": REMINDER}))
    elif advise.block:
        print(json.dumps({
            "context": (
                "TURN MODE: likely a question. Lead with a direct answer before "
                "doing any work. If you are about to act, say why first."
            )
        }))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
