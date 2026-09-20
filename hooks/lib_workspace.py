#!/usr/bin/env python3
"""Workspace path policy: where is a file allowed to be written, and does that
destination need to become a git repo first.

Pure decision logic. No side effects, no writes, no git mutations - the only
subprocess call is a read-only `git rev-parse` to detect an existing repo.
The caller (workspace_guard.py) performs any mkdir/init.

PYTHON 3.9 COMPATIBLE. Hooks run under whatever `python3` is on PATH, which on
macOS is /usr/bin/python3 = 3.9.6. No `X | None`, no match statements.
"""

from __future__ import annotations

import json
import os
import subprocess

# --------------------------------------------------------------------------
# Codes root resolution
# --------------------------------------------------------------------------

CONFIG_PATH = os.path.expanduser("~/.hermes/agent-hooks-state/workspace.json")

# Checked in order when nothing is configured. First existing dir wins, so a
# machine that already has a projects convention keeps it.
CANDIDATE_ROOTS = (
    "~/Documents/codes",
    "~/code",
    "~/Code",
    "~/Projects",
    "~/projects",
    "~/src",
    "~/dev",
)

DEFAULT_ROOT = "~/Documents/codes"


def read_config():
    """Return the hook config dict. Never raises."""
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def codes_root():
    """Resolve the project root directory.

    Order: HERMES_CODES_ROOT env -> workspace.json -> first existing candidate
    -> ~/Documents/codes. Never creates anything; resolution must stay pure so
    tests and the guard agree.
    """
    env = os.environ.get("HERMES_CODES_ROOT")
    if env:
        return os.path.abspath(os.path.expanduser(env))

    configured = read_config().get("codes_root")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))

    for cand in CANDIDATE_ROOTS:
        p = os.path.expanduser(cand)
        if os.path.isdir(p):
            return os.path.abspath(p)

    return os.path.abspath(os.path.expanduser(DEFAULT_ROOT))


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

# Files that are deliverables - things the user will want to open later.
DELIVERABLE_EXT = {
    ".md", ".html", ".htm", ".csv", ".tsv", ".json", ".py", ".ipynb",
    ".pdf", ".xlsx", ".pptx", ".docx", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".svg", ".yaml", ".yml", ".toml", ".sql", ".go", ".rs",
}

# Scratch in /tmp is fine and must never be gated.
SCRATCH_EXT = {".log", ".txt", ".sh", ".tmp", ".out", ".err", ".pid", ""}


def _always_allowed_prefixes():
    home = os.path.expanduser("~")
    return (
        os.path.join(home, ".hermes"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "hermes_runs"),
        os.path.join(home, "Library"),
        "/private/var",
        "/var",
        "/usr",
        "/etc",
        "/opt",
    )


class Verdict(object):
    """action: 'allow' | 'init' | 'block'."""

    def __init__(self, action, reason, repo_root=None, suggestion=""):
        self.action = action
        self.reason = reason
        self.repo_root = repo_root
        self.suggestion = suggestion

    @property
    def block(self):
        return self.action == "block"

    def __repr__(self):
        return "Verdict(%s, %r, repo_root=%r)" % (
            self.action, self.reason, self.repo_root,
        )


def _nearest_existing_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    while d and d != "/" and not os.path.isdir(d):
        d = os.path.dirname(d)
    return d or "/"


def git_toplevel(path):
    """Return the repo root containing *path*, or None. Read-only."""
    start = _nearest_existing_dir(path)
    try:
        out = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    top = out.stdout.decode("utf-8", "replace").strip()
    return os.path.abspath(top) if top else None


def _under(path, parent):
    path = os.path.abspath(path)
    parent = os.path.abspath(parent)
    return path == parent or path.startswith(parent + os.sep)


def _has_dot_component(path, below):
    """True if any component of path below *below* starts with a dot."""
    try:
        rel = os.path.relpath(path, below)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.split(os.sep) if part not in ("", "."))


def project_root_for(path, root=None):
    """Given a path under the codes root, return its project folder."""
    root = root or codes_root()
    path = os.path.abspath(os.path.expanduser(path))
    rel = os.path.relpath(path, root)
    first = rel.split(os.sep)[0]
    if first in ("", ".", ".."):
        return None
    return os.path.join(root, first)


def classify_path(path, root=None):
    """Decide what to do with a write to *path*.

    Order matters and is load-bearing:
      1. already inside a git repo      -> allow (never re-init, never touch branch)
      2. under the codes root           -> init  (the "no write without git" rule)
      3. scatter location               -> block (checked BEFORE the broad infra
                                          allowlist, which contains /var and so
                                          would otherwise shadow a HOME that
                                          lives under it - e.g. a test sandbox
                                          or an unusual account layout)
      4. /tmp                           -> scratch allow, deliverable block
      5. always-allowed location        -> allow (no git)
      6. anything else                  -> allow (stay permissive)
    """
    if not path:
        return Verdict("allow", "empty path")

    home = os.path.abspath(os.path.expanduser("~"))
    root = root or codes_root()
    p = os.path.abspath(os.path.expanduser(path))
    ext = os.path.splitext(p)[1].lower()

    # 1. Inside an existing repo: hands off.
    top = git_toplevel(p)
    if top:
        return Verdict("allow", "inside existing git repo", repo_root=top)

    # 2. Under the codes root but not yet a repo: this is the enforcement.
    if _under(p, root):
        proj = project_root_for(p, root)
        if proj:
            return Verdict("init", "project folder needs a git repo", repo_root=proj)
        return Verdict("allow", "codes root itself")

    # 3. Scatter: a deliverable written loose in HOME / Desktop / Documents.
    scatter_parents = (home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"))
    if os.path.dirname(p) in scatter_parents and ext in DELIVERABLE_EXT:
        where = os.path.dirname(p)
        return Verdict(
            "block",
            "deliverable written loose in %s" % where,
            suggestion=(
                "Put it in a project folder instead, e.g.\n"
                "  %s/<project-name>/%s" % (root, os.path.basename(p))
            ),
        )

    # 4. /tmp: scratch is fine, deliverables are not.
    if p.startswith("/tmp/") or p.startswith("/private/tmp/"):
        if ext in SCRATCH_EXT:
            return Verdict("allow", "tmp scratch file")
        return Verdict(
            "block",
            "deliverable in /tmp",
            suggestion="Deliverables belong in a project folder, not /tmp.",
        )

    # 5. Always-allowed locations.
    for prefix in _always_allowed_prefixes():
        if _under(p, prefix):
            return Verdict("allow", "infrastructure path")
    if _under(p, home) and _has_dot_component(p, home):
        return Verdict("allow", "dot-path")

    # 6. Permissive default.
    return Verdict("allow", "outside managed paths")
