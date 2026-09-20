#!/usr/bin/env python3
"""Choose and record the projects root for the workspace hooks.

    python3 scripts/setup_workspace.py            # interactive if a TTY
    python3 scripts/setup_workspace.py --yes      # never prompt, take the default
    python3 scripts/setup_workspace.py --root DIR # explicit

Resolution order, so a machine that already has a convention keeps it:
  1. --root argument
  2. HERMES_CODES_ROOT environment variable
  3. an existing ~/Documents/codes, ~/code, ~/Projects, ~/src, ...
  4. ask, when attached to a terminal
  5. ~/Documents/codes (created)

Writes the answer to ~/.hermes/agent-hooks-state/workspace.json and creates the
directory. Idempotent - safe to re-run.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

import lib_workspace as W  # noqa: E402


def detect_existing():
    found = []
    for cand in W.CANDIDATE_ROOTS:
        p = os.path.expanduser(cand)
        if os.path.isdir(p):
            try:
                n = len([
                    d for d in os.listdir(p)
                    if os.path.isdir(os.path.join(p, d)) and not d.startswith(".")
                ])
            except Exception:
                n = 0
            found.append((p, n))
    return found


def main():
    argv = sys.argv[1:]
    assume_yes = "--yes" in argv or "-y" in argv
    explicit = None
    if "--root" in argv:
        try:
            explicit = argv[argv.index("--root") + 1]
        except IndexError:
            print("--root needs a path", file=sys.stderr)
            return 2

    if explicit:
        root = os.path.abspath(os.path.expanduser(explicit))
        source = "--root"
    elif os.environ.get("HERMES_CODES_ROOT"):
        root = os.path.abspath(os.path.expanduser(os.environ["HERMES_CODES_ROOT"]))
        source = "HERMES_CODES_ROOT"
    else:
        existing = detect_existing()
        default = os.path.expanduser(W.DEFAULT_ROOT)
        if existing:
            root, count = existing[0]
            source = "detected (%d project%s)" % (count, "" if count == 1 else "s")
        else:
            root, source = default, "default"

        if not assume_yes and sys.stdin.isatty():
            print("Hermes projects root")
            print()
            if existing:
                print("Found these on this machine:")
                for p, n in existing:
                    print("  %s  (%d project%s)" % (p, n, "" if n == 1 else "s"))
                print()
            print("Every file Hermes creates will live in a git repo under this folder.")
            try:
                reply = input("Use [%s]? (enter to accept, or type a path): " % root).strip()
            except (EOFError, KeyboardInterrupt):
                reply = ""
            if reply:
                root = os.path.abspath(os.path.expanduser(reply))
                source = "user"

    try:
        os.makedirs(root, exist_ok=True)
    except Exception as exc:
        print("could not create %s: %s" % (root, exc), file=sys.stderr)
        return 1

    cfg_path = W.CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        cfg = W.read_config()
        cfg["codes_root"] = root
        tmp = cfg_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=1)
        os.replace(tmp, cfg_path)
    except Exception as exc:
        print("could not write %s: %s" % (cfg_path, exc), file=sys.stderr)
        return 1

    print("projects root: %s  (%s)" % (root, source))
    print("recorded in:   %s" % cfg_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
