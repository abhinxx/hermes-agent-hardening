#!/usr/bin/env python3
"""on_session_end hook -- the final sweep.

Catches everything pre_verify could not see:

  * files written through `terminal` (shell redirects, mv, build output) which
    never pass through write_file and so never reach the gate
  * deletions and renames done outside the file tools
  * the last turn's work when the session ends before verify runs

Runs EVEN WHEN interrupted. A user hitting /stop mid-work is exactly when
uncommitted work matters most.

This is also the only trigger that pushes. pre_verify commits without pushing
because pushing every turn is slow and noisy.

stdin:  {"hook_event_name": "on_session_end", "session_id": "...",
         "extra": {"interrupted": false, ...}}
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

    session_id = payload.get("session_id") or ""
    if not session_id:
        print("{}")
        return 0

    try:
        import lib_autogit as autogit
    except Exception as exc:
        sys.stderr.write("autogit-session: import failed: %s\n" % exc)
        print("{}")
        return 0

    try:
        repos = autogit.read_touched(session_id, consume=True)
    except Exception as exc:
        sys.stderr.write("autogit-session: %s\n" % exc)
        print("{}")
        return 0

    for repo in repos:
        try:
            autogit.commit_repo(repo, explicit_subject=None,
                                push=True, session_id=session_id)
        except Exception as exc:
            sys.stderr.write("autogit-session: %s: %s\n" % (repo, exc))

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
