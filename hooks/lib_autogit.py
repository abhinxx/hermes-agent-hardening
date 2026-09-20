#!/usr/bin/env python3
"""Shared git logic for the auto-commit hooks.

One public function, `commit_repo()`, used by every trigger. It never raises:
a failure here must never break the agent.

PYTHON 3.9 COMPATIBLE (macOS /usr/bin/python3 = 3.9.6).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time

STATE_DIR = os.path.expanduser("~/.hermes/agent-hooks-state")
LOCK_DIR = os.path.join(STATE_DIR, "gitlock")
RECEIPT_DIR = os.path.join(STATE_DIR, "receipt")
TOUCHED_DIR = os.path.join(STATE_DIR, "touched")
LOG_PATH = os.path.expanduser("~/.hermes/logs/auto_git.log")

TRAILER = "Shipped-by: hermes-autogit"
LOCK_TIMEOUT = 30

GITIGNORE = """\
.DS_Store
__pycache__/
*.pyc
.env
.env.*
!.env.example
node_modules/
.venv/
venv/
*.log
.ipynb_checkpoints/
"""

# --------------------------------------------------------------------------
# Secrets. The ONE fail-closed path in this design: a match aborts the commit.
# The file still exists on disk - we refuse to RECORD it, never to write it.
# --------------------------------------------------------------------------

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"), "anthropic key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "openai-style key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "github token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github pat"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "slack token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "jwt"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "google api key"),
]

# Placeholders that are meant to be committed.
PLACEHOLDER = re.compile(
    r"your[-_]?key|example|placeholder|xxxx|changeme|<[^>]+>|FAKE|dummy|sample",
    re.IGNORECASE,
)

TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except Exception:
        pass


def _git(repo, args, timeout=30):
    """Run a git command. Returns (rc, stdout). Never raises."""
    try:
        r = subprocess.run(
            ["git", "-C", repo] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except Exception as exc:
        _log("git %s failed in %s: %s" % (" ".join(args), repo, exc))
        return 1, ""


class _Lock(object):
    """Per-repo advisory lock. Parallel subagents otherwise collide on
    .git/index.lock and one of them dies."""

    def __init__(self, repo):
        key = hashlib.sha1(os.path.abspath(repo).encode("utf-8")).hexdigest()[:16]
        os.makedirs(LOCK_DIR, exist_ok=True)
        self.path = os.path.join(LOCK_DIR, key + ".lock")
        self.fh = None

    def __enter__(self):
        import fcntl
        try:
            self.fh = open(self.path, "w")
        except Exception:
            return self
        deadline = time.time() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except Exception:
                if time.time() > deadline:
                    _log("lock timeout on %s, proceeding without it" % self.path)
                    return self
                time.sleep(0.25)

    def __exit__(self, *exc):
        import fcntl
        if self.fh:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
                self.fh.close()
            except Exception:
                pass
        return False


def scan_secrets(diff_text):
    """Return a list of (label, filename) for secrets in a staged diff."""
    hits = []
    current = ""
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if current.endswith(TEMPLATE_SUFFIXES):
            continue
        body = line[1:]
        if PLACEHOLDER.search(body):
            continue
        for rx, label in SECRET_PATTERNS:
            if rx.search(body):
                hits.append((label, current))
                break
    return hits


def is_worktree(repo):
    """True inside a `hermes -w` worktree: git-dir differs from common-dir."""
    rc1, gd = _git(repo, ["rev-parse", "--absolute-git-dir"])
    rc2, cd = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if rc1 != 0 or rc2 != 0:
        return False
    return os.path.abspath(gd.strip()) != os.path.abspath(cd.strip())


def ensure_repo(project_root):
    """Create the folder and `git init` it if needed. Idempotent.

    Returns True when the folder is a git repo afterwards.
    """
    try:
        os.makedirs(project_root, exist_ok=True)
    except Exception as exc:
        _log("mkdir failed %s: %s" % (project_root, exc))
        return False

    if os.path.isdir(os.path.join(project_root, ".git")):
        return True

    with _Lock(project_root):
        # Re-check inside the lock: another agent may have won the race.
        if os.path.isdir(os.path.join(project_root, ".git")):
            return True
        rc, _ = _git(project_root, ["init", "-b", "main"])
        if rc != 0:
            rc, _ = _git(project_root, ["init"])   # older git without -b
        if rc != 0:
            _log("git init failed in %s" % project_root)
            return False
        gi = os.path.join(project_root, ".gitignore")
        if not os.path.exists(gi):
            try:
                with open(gi, "w") as f:
                    f.write(GITIGNORE)
            except Exception:
                pass
        _log("init %s" % project_root)
    return True


def _clean_subject(text, fallback):
    if not text:
        return fallback
    t = re.sub(r"```.*?```", " ", str(text), flags=re.DOTALL)
    t = re.sub(r"[*_`#>\[\]]", "", t)
    t = " ".join(t.split())
    if not t:
        return fallback

    # Walk sentences and take the first that reads like prose. The agent's
    # answer often opens with a bare path or a "file - 20 bytes" line, which
    # makes a useless commit subject; the real description is the next
    # sentence.
    for candidate in re.split(r"(?<=[.!?])\s+", t):
        c = candidate.strip()
        if not c:
            continue
        if _looks_like_prose(c):
            subject = c
            break
    else:
        # Nothing in the answer reads like a description (it was all paths or
        # metadata) -> use the deterministic file-list fallback instead.
        return fallback

    # A subject that would leak a secret into git log is replaced outright.
    for rx, _label in SECRET_PATTERNS:
        if rx.search(subject):
            return fallback
    if len(subject) > 72:
        subject = subject[:69].rstrip() + "..."
    return subject or fallback


def _looks_like_prose(s):
    """Reject path-like or metadata-like sentence fragments."""
    if len(s) < 12:
        return False
    if s.startswith("/") or s.startswith("~") or s.startswith("./"):
        return False
    # "path/to/file.md - 20 bytes, contains ..." style openers
    if re.match(r"^\S+\.[A-Za-z0-9]{1,5}\s*[-,:]", s):
        return False
    # mostly a path: several slashes and no spaces before the first one
    head = s.split(" ", 1)[0]
    if head.count("/") >= 2:
        return False
    return True


def build_subject(repo, explicit=None):
    """explicit text > file-list fallback."""
    rc, names = _git(repo, ["diff", "--cached", "--name-only"])
    files = [os.path.basename(n) for n in names.split() if n][:3] if rc == 0 else []
    if files:
        extra = ""
        total = len([n for n in names.split() if n])
        if total > 3:
            extra = " (+%d more)" % (total - 3)
        fallback = "hermes: update " + ", ".join(files) + extra
    else:
        fallback = "hermes: update"
    return _clean_subject(explicit, fallback)


def commit_repo(repo_root, explicit_subject=None, push=True, session_id=None):
    """Stage, scan, commit, optionally push. Never raises.

    Returns a dict receipt describing what happened.
    """
    receipt = {
        "repo": repo_root,
        "name": os.path.basename(repo_root.rstrip("/")),
        "status": "noop",
        "sha": "",
        "count": 0,
        "pushed": False,
    }
    try:
        if not os.path.isdir(os.path.join(repo_root, ".git")):
            if not ensure_repo(repo_root):
                receipt["status"] = "error"
                return receipt

        with _Lock(repo_root):
            rc, _ = _git(repo_root, ["add", "-A"])
            if rc != 0:
                receipt["status"] = "error"
                return receipt

            rc, _ = _git(repo_root, ["diff", "--cached", "--quiet"])
            if rc == 0:
                return receipt              # nothing staged, no empty commit

            _rc, diff = _git(repo_root, ["diff", "--cached", "-U0"], timeout=60)
            hits = scan_secrets(diff)
            if hits:
                _git(repo_root, ["reset"])
                receipt["status"] = "held"
                receipt["reason"] = "%s in %s" % (hits[0][0], hits[0][1] or "staged diff")
                _log("HELD %s: %s" % (repo_root, receipt["reason"]))
                return receipt

            subject = build_subject(repo_root, explicit_subject)
            rc, _ = _git(repo_root, ["commit", "-m", subject, "-m", TRAILER])
            if rc != 0:
                receipt["status"] = "error"
                return receipt

            _rc, sha = _git(repo_root, ["rev-parse", "--short", "HEAD"])
            _rc2, cnt = _git(repo_root, ["rev-list", "--count", "HEAD"])
            receipt["sha"] = sha.strip()
            try:
                receipt["count"] = int(cnt.strip())
            except Exception:
                receipt["count"] = 0
            receipt["status"] = "committed"
            receipt["subject"] = subject
            _log("commit %s %s '%s'" % (repo_root, receipt["sha"], subject))

            if push and not is_worktree(repo_root):
                rc, _ = _git(repo_root, ["remote", "get-url", "origin"], timeout=10)
                if rc == 0:
                    try:
                        subprocess.Popen(
                            ["git", "-C", repo_root, "push", "-q"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        receipt["pushed"] = True
                        _log("push started %s" % repo_root)
                    except Exception as exc:
                        _log("push spawn failed %s: %s" % (repo_root, exc))
    except Exception as exc:
        _log("commit_repo crashed %s: %s" % (repo_root, exc))
        receipt["status"] = "error"

    if session_id:
        write_receipt(session_id, receipt)
    return receipt


# --------------------------------------------------------------------------
# Receipts: how the footer hook learns what happened without touching git.
# --------------------------------------------------------------------------

def write_receipt(session_id, receipt):
    if receipt.get("status") not in ("committed", "held"):
        return
    try:
        os.makedirs(RECEIPT_DIR, exist_ok=True)
        path = os.path.join(RECEIPT_DIR, "%s.json" % session_id)
        existing = []
        if os.path.exists(path):
            try:
                with open(path) as f:
                    existing = json.load(f)
            except Exception:
                existing = []
        if not isinstance(existing, list):
            existing = []
        existing = [r for r in existing if r.get("repo") != receipt.get("repo")]
        existing.append(receipt)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(existing, f)
        os.replace(tmp, path)
    except Exception as exc:
        _log("receipt write failed: %s" % exc)


def read_receipts(session_id, consume=True):
    path = os.path.join(RECEIPT_DIR, "%s.json" % session_id)
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return []
    if consume:
        try:
            os.remove(path)
        except Exception:
            pass
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------
# Touched-repo tracking: what the session-end sweep operates on.
# --------------------------------------------------------------------------

def record_touched(session_id, repo_root):
    if not session_id or not repo_root:
        return
    try:
        os.makedirs(TOUCHED_DIR, exist_ok=True)
        with open(os.path.join(TOUCHED_DIR, "%s.txt" % session_id), "a") as f:
            f.write(os.path.abspath(repo_root) + "\n")
    except Exception:
        pass


def read_touched(session_id, consume=True):
    path = os.path.join(TOUCHED_DIR, "%s.txt" % session_id)
    try:
        with open(path) as f:
            roots = [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return []
    if consume:
        try:
            os.remove(path)
        except Exception:
            pass
    seen, out = set(), []
    for r in roots:
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out
