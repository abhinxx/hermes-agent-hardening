#!/usr/bin/env python3
"""pre_tool_call hook -- the workspace gate.

Two jobs, in one place, before the write happens:

  1. REFUSE deliverables written loose in $HOME / Desktop / Documents / tmp.
  2. GUARANTEE that anything written under the codes root lands in a git repo -
     creating the folder and running `git init` if needed.

Job 2 is the "no write without git" rule. It is a side effect of the gate, not
a request to the agent: the model is never asked, never told, and cannot
forget. By the time write_file runs, the repo exists.

Fail OPEN: any unexpected error allows the write. This is a discipline gate,
not a security boundary. Do not set fail_closed on it.

stdin:  {"tool_name": "write_file", "tool_input": {"path": "..."}, "session_id": "..."}
stdout: {} on allow
exit 2 + stderr on block
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WATCHED = ("write_file", "patch")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    tool = payload.get("tool_name") or ""
    if tool not in WATCHED:
        print("{}")
        return 0

    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("path") or ""
    if not path:
        print("{}")
        return 0

    session_id = payload.get("session_id") or ""

    try:
        from lib_workspace import classify_path, codes_root
        import lib_autogit as autogit
    except Exception as exc:
        sys.stderr.write("workspace-guard: import failed: %s\n" % exc)
        print("{}")
        return 0

    try:
        verdict = classify_path(path)
    except Exception as exc:
        sys.stderr.write("workspace-guard: classify failed: %s\n" % exc)
        print("{}")
        return 0

    if verdict.action == "block":
        root = codes_root()
        sys.stderr.write(
            "BLOCKED by workspace-guard: %s\n\n"
            "%s\n\n"
            "Deliverables live in a project folder, which is created and git-init'd\n"
            "for you automatically. Just write to:\n"
            "  %s/<project-name>/<file>\n"
            "No mkdir or git init needed - writing there is enough.\n"
            % (verdict.reason, verdict.suggestion, root)
        )
        return 2

    if verdict.action == "init" and verdict.repo_root:
        try:
            autogit.ensure_repo(verdict.repo_root)
        except Exception as exc:
            sys.stderr.write("workspace-guard: init failed: %s\n" % exc)

    if verdict.repo_root and session_id:
        try:
            autogit.record_touched(session_id, verdict.repo_root)
        except Exception:
            pass

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
