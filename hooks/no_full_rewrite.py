#!/usr/bin/env python3
"""pre_tool_call hook -- refuse full-file rewrites of large existing files.

THE FAILURE THIS PREVENTS
  Three consecutive write_file calls on a 51KB HTML document, to change a
  colour. Each streamed for minutes, showed the user only a spinner, and was
  interrupted before completion. Net result: zero bytes written, ~10 minutes
  gone, and no tool-result row in the session DB to even show what happened.

RULE
  write_file on a file that already exists and exceeds THRESHOLD_BYTES is
  blocked. Use patch for a targeted edit, or delete the file first if a real
  rewrite is intended.

Creating new files is never blocked. Small files are never blocked.

ESCAPE HATCHES
  - delete the file first, then write_file creates it fresh
  - raise the threshold via HERMES_REWRITE_MAX_BYTES
  - add a path substring to ~/.hermes/agent-hooks-state/rewrite-allow.txt

stdin:  {"tool_name": "write_file", "tool_input": {"path": "...", "content": "..."}}
exit 2 + stderr on block
"""

import json
import os
import sys

THRESHOLD_BYTES = int(os.environ.get("HERMES_REWRITE_MAX_BYTES", "15000"))
ALLOWLIST = os.path.expanduser("~/.hermes/agent-hooks-state/rewrite-allow.txt")


def allowlisted(path: str) -> bool:
    try:
        with open(ALLOWLIST) as f:
            for line in f:
                frag = line.strip()
                if frag and not frag.startswith("#") and frag in path:
                    return True
    except FileNotFoundError:
        pass
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    if (payload.get("tool_name") or "") != "write_file":
        print("{}")
        return 0

    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("path") or ""
    if not path:
        print("{}")
        return 0

    path = os.path.expanduser(path)

    if not os.path.exists(path):
        print("{}")          # creating a new file is always fine
        return 0

    if allowlisted(path):
        print("{}")
        return 0

    try:
        size = os.path.getsize(path)
    except OSError:
        print("{}")
        return 0

    if size <= THRESHOLD_BYTES:
        print("{}")
        return 0

    new_len = len(tool_input.get("content") or "")

    sys.stderr.write(
        f"BLOCKED by no-full-rewrite: {path} already exists and is "
        f"{size:,} bytes (limit {THRESHOLD_BYTES:,}).\n"
        f"You tried to overwrite the whole thing with {new_len:,} bytes.\n"
        f"A full rewrite of a file this size streams for minutes, shows the "
        f"user nothing but a spinner, and writes ZERO bytes if interrupted.\n"
        f"Use patch for the specific change instead. For a genuine rewrite, "
        f"delete the file first or add a fragment of its path to\n"
        f"  {ALLOWLIST}\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
