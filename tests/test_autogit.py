#!/usr/bin/env python3
"""Tests for the auto-git core: init, commit, secrets gate, worktree, locking.

    python3 tests/test_autogit.py

Operates entirely inside a temp dir. Never touches the real ~/.hermes or any
real repo.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

import lib_autogit as A  # noqa: E402

PASS = 0
FAIL = 0


def check(desc, got, want):
    global PASS, FAIL
    if got == want:
        print("  ok   - %s" % desc)
        PASS += 1
    else:
        print("  FAIL - %s (got %r, want %r)" % (desc, got, want))
        FAIL += 1


def truthy(desc, got):
    check(desc, bool(got), True)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                          ).stdout.decode()


def main():
    sandbox = tempfile.mkdtemp(prefix="hermes-autogit-test-")
    # Redirect all hook state into the sandbox.
    A.STATE_DIR = os.path.join(sandbox, "state")
    A.LOCK_DIR = os.path.join(A.STATE_DIR, "gitlock")
    A.RECEIPT_DIR = os.path.join(A.STATE_DIR, "receipt")
    A.TOUCHED_DIR = os.path.join(A.STATE_DIR, "touched")
    A.LOG_PATH = os.path.join(sandbox, "auto_git.log")

    try:
        print("ensure_repo")
        proj = os.path.join(sandbox, "codes", "demo")
        truthy("creates and inits", A.ensure_repo(proj))
        truthy(".git exists", os.path.isdir(os.path.join(proj, ".git")))
        truthy(".gitignore written", os.path.exists(os.path.join(proj, ".gitignore")))
        truthy("idempotent second call", A.ensure_repo(proj))

        # identity for commits inside the sandbox
        subprocess.run(["git", "-C", proj, "config", "user.email", "t@example.com"])
        subprocess.run(["git", "-C", proj, "config", "user.name", "Test"])

        print("\ncommit_repo")
        with open(os.path.join(proj, "a.md"), "w") as f:
            f.write("hello\n")
        r = A.commit_repo(proj, explicit_subject=None, push=False, session_id="s1")
        check("status committed", r["status"], "committed")
        truthy("sha present", r["sha"])
        check("count is 1", r["count"], 1)
        truthy("trailer present", A.TRAILER in git(proj, "log", "-1", "--format=%B"))

        print("\nempty commit is skipped")
        r2 = A.commit_repo(proj, push=False, session_id="s1")
        check("noop when nothing changed", r2["status"], "noop")
        check("still 1 commit", git(proj, "rev-list", "--count", "HEAD").strip(), "1")

        print("\nsubject from explicit text")
        with open(os.path.join(proj, "b.md"), "w") as f:
            f.write("more\n")
        r3 = A.commit_repo(proj, explicit_subject="Fixed the department headings. Then more.",
                           push=False, session_id="s1")
        check("first sentence used", r3.get("subject"),
              "Fixed the department headings.")

        print("\nsubject skips a path-like opener (regression: real session)")
        check("path opener skipped",
              A._clean_subject(
                  "/Users/x/Documents/codes/demo/hello.md - 20 bytes, contains hi. "
                  "Created the greeting file.", "FB"),
              "Created the greeting file.")
        check("bare path falls through to fallback",
              A._clean_subject("/Users/x/codes/demo/a.md", "FB"), "FB")
        check("normal prose untouched",
              A._clean_subject("Built the comparison page.", "FB"),
              "Built the comparison page.")

        print("\nsecrets gate (THE fail-closed path)")
        with open(os.path.join(proj, "leak.py"), "w") as f:
            f.write('KEY = "sk-ant-api03-Bq7xR2mTvL9pWzYn4KdHsEjA"\n')
        before = git(proj, "rev-list", "--count", "HEAD").strip()
        r4 = A.commit_repo(proj, push=False, session_id="s1")
        check("commit held", r4["status"], "held")
        check("no new commit", git(proj, "rev-list", "--count", "HEAD").strip(), before)
        truthy("file still on disk", os.path.exists(os.path.join(proj, "leak.py")))
        check("nothing left staged", git(proj, "diff", "--cached", "--name-only").strip(), "")
        os.remove(os.path.join(proj, "leak.py"))

        print("\nsecrets: placeholders and templates are exempt")
        check("placeholder ignored",
              A.scan_secrets('+++ b/x.py\n+KEY = "sk-ant-your-key-here"\n'), [])
        check("template file ignored",
              A.scan_secrets('+++ b/.env.example\n+AWS=AKIAIOSFODNN7EXAMPLE\n'), [])
        truthy("real aws key caught",
               A.scan_secrets('+++ b/prod.py\n+AWS=AKIAQYLPMN5HXYZABCDE\n'))

        print("\nsubject never leaks a secret into git log")
        s = A.build_subject(proj, explicit="my key is sk-ant-api03-Bq7xR2mTvL9pWzYn4KdHsEjA")
        truthy("secret-bearing subject replaced", "sk-ant" not in s)

        print("\nreceipts")
        A.RECEIPT_DIR = os.path.join(A.STATE_DIR, "receipt")
        recs = A.read_receipts("s1", consume=True)
        truthy("receipts recorded", len(recs) >= 1)
        check("consumed", A.read_receipts("s1"), [])

        print("\ntouched tracking")
        A.record_touched("s2", proj)
        A.record_touched("s2", proj)
        t = A.read_touched("s2")
        check("deduped", t, [os.path.abspath(proj)])
        check("consumed", A.read_touched("s2"), [])

        print("\nworktree detection")
        check("normal repo is not a worktree", A.is_worktree(proj), False)
        wt = os.path.join(sandbox, "wt")
        subprocess.run(["git", "-C", proj, "worktree", "add", "-q", "-b", "hermes/x", wt],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.isdir(wt):
            check("worktree detected", A.is_worktree(wt), True)
        else:
            print("  skip - worktree add unavailable")

    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print("\npassed: %d   failed: %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
