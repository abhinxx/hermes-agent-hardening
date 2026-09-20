"""hermes-autogit plugin: stamp replies with what was committed.

WHY A PLUGIN AND NOT A SHELL HOOK
  `transform_llm_output` consumes a plain `str` return value
  (agent/turn_finalizer.py:607 - "first hook to return a string wins").
  Shell-hook callbacks can only ever return `Optional[Dict]`
  (agent/shell_hooks.py:627), and the shell-result normaliser understands only
  block / modify / continue / context. So a shell hook physically cannot
  replace response text. This one behaviour has to be in-process.

Every other part of auto-git stays a shell hook. This plugin is read-only: it
makes no git calls, it just reads the receipt that lib_autogit wrote during the
commit and appends one line per repo.

Appends to the response, never replaces it. Returning a bare footer here would
delete the assistant's entire message.
"""

import os
import sys

HOOKS_DIR = os.path.expanduser("~/.hermes/agent-hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


def _format_line(r):
    name = r.get("name") or os.path.basename((r.get("repo") or "repo").rstrip("/"))
    status = r.get("status")

    if status == "held":
        return "[git] %s · HELD: %s not committed" % (
            name, r.get("reason", "secret detected"),
        )
    if status != "committed":
        return ""

    bits = ["[git] %s @ %s" % (name, r.get("sha") or "?")]
    if r.get("count"):
        bits.append("commit #%d" % r["count"])
    bits.append("pushed" if r.get("pushed") else "local only")
    return " · ".join(bits)


def _footer(response_text="", session_id="", **_kwargs):
    """Return response_text + footer, or None to leave the reply untouched."""
    if not session_id or not isinstance(response_text, str) or not response_text.strip():
        return None

    try:
        import lib_autogit as autogit
    except Exception:
        return None

    try:
        receipts = autogit.read_receipts(session_id, consume=True)
    except Exception:
        return None

    lines = [ln for ln in (_format_line(r) for r in receipts) if ln]
    if not lines:
        return None

    # CRITICAL: original first. A bare footer would replace the whole message.
    return response_text.rstrip() + "\n\n" + "\n".join(lines)


def register(ctx):
    ctx.register_hook("transform_llm_output", _footer)
