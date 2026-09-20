#!/usr/bin/env python3
"""pre_verify hook -- commit the turn's work.

This is the completion trigger. Hermes fires pre_verify once per turn, only
when the agent edited files, immediately before the turn's answer is accepted -
and hands over the list of changed paths. That is exactly one unit of completed
work, so it is exactly one commit.

MUST return {} - a continue/block directive would nudge the agent into another
turn, which is not what this hook is for. It commits and gets out of the way.

MUST be idempotent on `attempt`: the hook re-fires after each verify nudge, so
it only acts on attempt 0.

stdin:  {"hook_event_name": "pre_verify", "session_id": "...",
         "extra": {"attempt": 0, "changed_paths": [...], "final_response": "..."}}
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

    extra = payload.get("extra") or {}
    session_id = payload.get("session_id") or ""

    try:
        if int(extra.get("attempt") or 0) != 0:
            print("{}")            # already handled on the first attempt
            return 0
    except Exception:
        pass

    changed = extra.get("changed_paths") or []
    if not isinstance(changed, list) or not changed:
        print("{}")
        return 0

    try:
        from lib_workspace import classify_path
        import lib_autogit as autogit
    except Exception as exc:
        sys.stderr.write("autogit-verify: import failed: %s\n" % exc)
        print("{}")
        return 0

    # Group the turn's changed files by the repo that owns them.
    repos = []
    for p in changed:
        try:
            v = classify_path(str(p))
        except Exception:
            continue
        if v.repo_root and v.repo_root not in repos:
            repos.append(v.repo_root)

    subject = extra.get("final_response") or ""

    for repo in repos:
        try:
            # push=False: pushing every turn is slow and noisy. Session end pushes.
            autogit.commit_repo(repo, explicit_subject=subject,
                                push=False, session_id=session_id)
        except Exception as exc:
            sys.stderr.write("autogit-verify: %s: %s\n" % (repo, exc))

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
