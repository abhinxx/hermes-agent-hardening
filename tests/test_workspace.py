#!/usr/bin/env python3
"""Tests for the workspace path policy.

    python3 tests/test_workspace.py

Uses a sandboxed fake HOME so nothing touches the real one. Plain asserts, no
pytest dependency, matching tests/test_classifier.py.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

import lib_workspace as W  # noqa: E402

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


def main():
    sandbox = tempfile.mkdtemp(prefix="hermes-ws-test-")
    home = os.path.join(sandbox, "home")
    root = os.path.join(home, "Documents", "codes")
    os.makedirs(root)
    os.makedirs(os.path.join(home, "Desktop"))
    os.makedirs(os.path.join(home, "Downloads"))
    os.makedirs(os.path.join(home, ".hermes"))

    old_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    os.environ["HERMES_CODES_ROOT"] = root
    # The real ~/.hermes/agent-hooks-state/workspace.json outranks candidate
    # detection, so point CONFIG_PATH into the sandbox or this test depends on
    # whether the tool happens to be installed on the machine running it.
    old_cfg = W.CONFIG_PATH
    W.CONFIG_PATH = os.path.join(sandbox, "workspace.json")

    try:
        print("codes root resolution")
        check("env var wins", W.codes_root(), root)

        os.environ.pop("HERMES_CODES_ROOT")
        check("falls back to existing ~/Documents/codes", W.codes_root(), root)
        os.environ["HERMES_CODES_ROOT"] = root

        print("\nblocking scatter")
        check("deliverable loose in HOME",
              W.classify_path(os.path.join(home, "report.html"), root).action, "block")
        check("deliverable on Desktop",
              W.classify_path(os.path.join(home, "Desktop", "notes.md"), root).action, "block")
        check("deliverable in Documents root",
              W.classify_path(os.path.join(home, "Documents", "data.csv"), root).action, "block")
        check("deliverable in /tmp",
              W.classify_path("/tmp/final_report.html", root).action, "block")

        print("\nallowing infrastructure and scratch")
        check("hermes config",
              W.classify_path(os.path.join(home, ".hermes", "config.yaml"), root).action, "allow")
        check("downloads",
              W.classify_path(os.path.join(home, "Downloads", "paper.pdf"), root).action, "allow")
        check("tmp scratch log",
              W.classify_path("/tmp/debug.log", root).action, "allow")
        check("dotfile in home",
              W.classify_path(os.path.join(home, ".zshrc"), root).action, "allow")
        check("non-deliverable loose in HOME is not blocked",
              W.classify_path(os.path.join(home, "somefile.dat"), root).action, "allow")

        print("\ncodes root triggers init")
        v = W.classify_path(os.path.join(root, "myproj", "index.html"), root)
        check("new project needs init", v.action, "init")
        check("repo root resolved to project folder", v.repo_root,
              os.path.join(root, "myproj"))

        v2 = W.classify_path(os.path.join(root, "myproj", "src", "deep", "a.py"), root)
        check("nested file resolves to same project root", v2.repo_root,
              os.path.join(root, "myproj"))

        v3 = W.classify_path(os.path.join(root, "myproj", ".github", "ci.yml"), root)
        check("dot dir inside a project still inits", v3.action, "init")

        print("\nexisting repo is left alone")
        repo = os.path.join(root, "existing")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        v4 = W.classify_path(os.path.join(repo, "file.md"), root)
        check("existing repo -> allow, not init", v4.action, "allow")
        check("existing repo root detected", v4.repo_root, os.path.realpath(repo))

        print("\nproject_root_for")
        check("resolves first component",
              W.project_root_for(os.path.join(root, "abc", "x", "y.md"), root),
              os.path.join(root, "abc"))

    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        os.environ.pop("HERMES_CODES_ROOT", None)
        W.CONFIG_PATH = old_cfg
        shutil.rmtree(sandbox, ignore_errors=True)

    print("\npassed: %d   failed: %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
