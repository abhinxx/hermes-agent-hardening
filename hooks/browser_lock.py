#!/usr/bin/env python3
"""pre_tool_call hook -- enforce single-owner access to the local browser.

THE FAILURE THIS PREVENTS
  Nine subagents dispatched in parallel. Three reached for the browser
  unprompted despite the parent's instruction that only one agent owned it.
  They raced on the same tab, clobbered each other's navigation, and the user
  had to catch it from a screenshot and shout the rule a second time.

  A prompt-level rule ("DO NOT use the browser, another agent owns it") is
  advice. Subagents ignore advice. A lockfile is not advice.

RULE
  The first session to call browser_exec takes the lock. Any other session
  calling browser_exec while the lock is held and fresh is blocked.
  The lock auto-expires after STALE_SECONDS so a crashed owner cannot deadlock
  the browser forever.

Named browser sessions (session=<name> in the browser_exec call) are isolated
by design and get their own lock key, so genuinely parallel work still works.

NOTE ON fail_closed
  This is the ONE hook in the set worth running with fail_closed: true. If the
  lock cannot be evaluated, blocking is the safe outcome: a browser race costs
  more than a refused call.

stdin:  {"tool_name": "browser_exec", "tool_input": {...}, "session_id": "..."}
exit 2 + stderr on block
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.hermes/agent-hooks-state")
STALE_SECONDS = int(os.environ.get("HERMES_BROWSER_LOCK_TTL", "900"))


def lock_path(key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return os.path.join(STATE_DIR, f"browser.{safe}.lock")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    if (payload.get("tool_name") or "") != "browser_exec":
        print("{}")
        return 0

    session_id = payload.get("session_id") or "unknown"
    tool_input = payload.get("tool_input") or {}
    browser_session = tool_input.get("session") or "default"

    os.makedirs(STATE_DIR, exist_ok=True)
    path = lock_path(browser_session)
    now = time.time()

    holder = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                holder = json.load(f)
        except Exception:
            holder = None

    if holder:
        age = now - float(holder.get("ts", 0))
        owner = holder.get("session_id")
        if owner and owner != session_id and age < STALE_SECONDS:
            sys.stderr.write(
                f"BLOCKED by browser-lock: the '{browser_session}' browser is "
                f"owned by session {owner} (held {int(age)}s ago).\n"
                f"Only ONE agent may drive a browser session at a time - "
                f"parallel drivers race on the same tab and corrupt each "
                f"other's navigation.\n"
                f"Either wait for that agent to finish, or use a distinct "
                f"named session: browser_exec(session='my-task-name', ...).\n"
            )
            return 2

    # Take or refresh the lock.
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"session_id": session_id, "ts": now,
                       "browser_session": browser_session}, f)
        os.replace(tmp, path)
    except Exception as exc:
        sys.stderr.write(f"browser-lock: could not write lock: {exc}\n")

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
