#!/usr/bin/env python3
"""subagent_stop hook -- commit a child's work when it exits.

A subagent's work ends outside the parent's turn, so pre_verify never sees it.
Without this, a child's output sits uncommitted until the whole session ends -
and if several children touch the same repo, their work gets merged into one
indistinguishable commit.

Commits the parent session's touched repos as they stand when the child exits,
tagged with the child id so `git log` shows which worker produced what.

Observer hook: return value ignored, never blocks.

stdin:  {"hook_event_name": "subagent_stop", "session_id": "<parent>",
         "extra": {"child_session_id": "...", "child_status": "...", ...}}
stdout: {}
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    parent = payload.get("session_id") or ""
    extra = payload.get("extra") or {}
    child = str(extra.get("child_session_id") or "")[:24]
    summary = extra.get("child_summary") or ""

    if not parent:
        print("{}")
        return 0

    try:
        import lib_autogit as autogit
    except Exception as exc:
        sys.stderr.write("autogit-subagent: import failed: %s\n" % exc)
        print("{}")
        return 0

    # Read without consuming: the session-end sweep still needs these.
    try:
        repos = autogit.read_touched(parent, consume=False)
    except Exception:
        repos = []

    subject = None
    if isinstance(summary, str) and summary.strip():
        subject = summary.strip()
    elif child:
        subject = "subagent %s finished" % child

    for repo in repos:
        try:
            autogit.commit_repo(repo, explicit_subject=subject,
                                push=False, session_id=parent)
        except Exception as exc:
            sys.stderr.write("autogit-subagent: %s: %s\n" % (repo, exc))

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
