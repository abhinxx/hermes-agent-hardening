#!/usr/bin/env python3
"""Append the hooks block to config.yaml without a YAML dependency.

    python3 scripts/merge_hooks_config.py <config.yaml> <hooks.yaml>

System python3 on macOS has no PyYAML, and we refuse to make the installer
depend on a pip install into a system interpreter. The hooks block is appended
verbatim as text, which is safe because:

  * we verify no top-level `hooks:` key already exists (the installer checks,
    and so does this script)
  * the block is valid YAML on its own at top level
  * comments are preserved, unlike a parse-and-redump round trip

Refuses to run if a `hooks:` key is already present. Merge by hand in that case.
"""

import os
import re
import sys


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    config_path, hooks_path = sys.argv[1], sys.argv[2]

    if not os.path.exists(config_path):
        print(f"config not found: {config_path}", file=sys.stderr)
        return 1
    if not os.path.exists(hooks_path):
        print(f"hooks snippet not found: {hooks_path}", file=sys.stderr)
        return 1

    with open(config_path) as f:
        config = f.read()

    if re.search(r"^hooks:", config, re.M):
        print("config.yaml already contains a top-level 'hooks:' key.",
              file=sys.stderr)
        print("Merge config/hooks.yaml manually.", file=sys.stderr)
        return 1

    with open(hooks_path) as f:
        snippet = f.read()

    # Strip the leading comment header; keep the body from `hooks:` onward so
    # the appended text is unambiguous at top level.
    idx = snippet.find("\nhooks:")
    body = snippet[idx + 1:] if idx != -1 else snippet

    if not config.endswith("\n"):
        config += "\n"

    out = config + "\n# --- installed by hermes-agent-hardening ---\n" + body
    if not out.endswith("\n"):
        out += "\n"

    with open(config_path, "w") as f:
        f.write(out)

    print(f"appended hooks block to {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
