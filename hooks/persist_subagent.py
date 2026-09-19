#!/usr/bin/env python3
"""subagent_stop hook -- persist every subagent's output to durable storage.

THE FAILURE THIS PREVENTS
  Nine subagents produced research findings. The parent read only the
  truncated summaries that fit in its context; the full output lived in a
  transient cache directory. When the user asked where the data was, there was
  no durable answer. Work that was paid for twice.

RULE
  On every child exit, append its summary and metadata to
  ~/hermes_runs/<parent_session>/subagents.jsonl and write a readable
  per-child markdown file alongside it.

Observer hook. Return value ignored. Never blocks anything.

stdin:  {"hook_event_name": "subagent_stop", "session_id": "...",
         "extra": {"child_session_id": ..., "child_role": ...,
                   "child_summary": ..., "child_status": ...,
                   "duration_ms": ..., "tool_call_history": [...]}}
stdout: {}
"""

import json
import os
import sys
import time

RUNS_DIR = os.path.expanduser(os.environ.get("HERMES_RUNS_DIR", "~/hermes_runs"))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    parent = payload.get("session_id") or "unknown"
    extra = payload.get("extra") or {}

    child_id = extra.get("child_session_id") or "unknown-child"
    summary = extra.get("child_summary") or ""
    status = extra.get("child_status") or "unknown"
    role = extra.get("child_role") or "leaf"
    duration_ms = extra.get("duration_ms") or 0
    history = extra.get("tool_call_history") or []

    try:
        out_dir = os.path.join(RUNS_DIR, parent)
        os.makedirs(out_dir, exist_ok=True)

        record = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "parent_session": parent,
            "child_session": child_id,
            "role": role,
            "status": status,
            "duration_ms": duration_ms,
            "tool_calls": len(history) if isinstance(history, list) else 0,
            "summary": summary,
        }

        with open(os.path.join(out_dir, "subagents.jsonl"), "a") as f:
            f.write(json.dumps(record) + "\n")

        # Human-readable copy. The jsonl is for tooling; this is for reading.
        safe_child = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(child_id))
        md = os.path.join(out_dir, f"{safe_child}.md")
        with open(md, "w") as f:
            f.write(f"# Subagent {child_id}\n\n")
            f.write(f"- parent: `{parent}`\n")
            f.write(f"- role: {role}\n")
            f.write(f"- status: **{status}**\n")
            f.write(f"- duration: {duration_ms} ms\n")
            f.write(f"- tool calls: {record['tool_calls']}\n")
            f.write(f"- finished: {record['iso']}\n\n")
            f.write("## Summary\n\n")
            f.write(summary if summary else "_(empty summary)_\n")
            f.write("\n")

            if isinstance(history, list) and history:
                f.write("\n## Tool call history\n\n")
                for item in history[:200]:
                    if isinstance(item, dict):
                        name = item.get("tool") or item.get("name") or "?"
                        f.write(f"- `{name}`\n")
                    else:
                        f.write(f"- `{item}`\n")
    except Exception as exc:
        sys.stderr.write(f"persist-subagent: {exc}\n")

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
