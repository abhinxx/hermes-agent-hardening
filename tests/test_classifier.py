#!/usr/bin/env python3
"""Regression tests for the turn classifier.

The zero-false-block property of the STRICT tier is the whole product. If that
regresses, the question-gate is unshippable. Run this after ANY edit to
lib_classify.py:

    python3 tests/test_classifier.py

Corpus: 56 hand-labelled real user messages from session 20260917_143455_37e2bc,
a 3h25m session in which the agent fired tools on 20 separate turns where the
user had asked a question or said stop.

  Q = pure question / stop / demand for an account  -> SHOULD block
  W = work order (may be phrased angrily or as a question) -> MUST NOT block
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

from lib_classify import classify  # noqa: E402

CORPUS = os.path.join(HERE, "corpus_session_37e2bc.json")

# Precision on the STRICT tier must be perfect. Recall is allowed to be
# mediocre: a missed question costs one reminder, a false block costs trust.
MIN_STRICT_PRECISION = 1.00
MIN_STRICT_RECALL = 0.55
MIN_ADVISE_RECALL = 0.75


def evaluate(rows, tier):
    tp = fp = tn = fn = 0
    false_blocks = []
    for row in rows:
        want = row["label"] == "Q"
        got = classify(row["text"], tier=tier).block
        if got and want:
            tp += 1
        elif got and not want:
            fp += 1
            false_blocks.append(row)
        elif not got and want:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall,
        "accuracy": (tp + tn) / len(rows),
        "false_blocks": false_blocks,
    }


def test_synthetic_invariants():
    """Cases that must hold regardless of corpus drift."""
    must_allow = [
        "make it light mode",
        "fix the department headings",
        "use linkup skill",
        "add details to the file",
        "now extremely carefully execute html file update",
        "do the fix, this is your last shot",
        "GO: rewrite the whole thing",          # override beats everything
        "why is this broken? DO IT: fix it",    # override inside a question
        "so do web search and figure out",
        "cross-check every single piece of information",
    ]
    must_block = [
        "what did you do so far",
        "whats the session id",
        "stop",
        "hold on answer my fucking question",
        "why did you stop?",
        "is it done or what? whats going on?",
        "ANSWER ONLY: what is your plan",       # explicit lock
        "if you were in my shoes what would you do",
    ]
    failures = []
    for t in must_allow:
        if classify(t, tier="strict").block:
            failures.append(("should ALLOW but blocked", t))
    for t in must_block:
        if not classify(t, tier="strict").block:
            failures.append(("should BLOCK but allowed", t))
    return failures


def main():
    with open(CORPUS) as f:
        rows = [r for r in json.load(f) if r.get("label") in ("Q", "W")]

    print(f"corpus: {len(rows)} labelled messages "
          f"({sum(1 for r in rows if r['label'] == 'Q')} questions, "
          f"{sum(1 for r in rows if r['label'] == 'W')} work orders)\n")

    ok = True

    strict = evaluate(rows, "strict")
    print(f"STRICT  accuracy={strict['accuracy']:.0%}  "
          f"precision={strict['precision']:.0%}  recall={strict['recall']:.0%}  "
          f"false_blocks={strict['fp']}")
    for row in strict["false_blocks"]:
        print(f"   FALSE BLOCK [{row['ts']}] {row['text'][:70]!r}")

    advise = evaluate(rows, "advise")
    print(f"ADVISE  accuracy={advise['accuracy']:.0%}  "
          f"precision={advise['precision']:.0%}  recall={advise['recall']:.0%}  "
          f"false_positives={advise['fp']}")

    print()
    if strict["precision"] < MIN_STRICT_PRECISION:
        print(f"FAIL: strict precision {strict['precision']:.0%} < {MIN_STRICT_PRECISION:.0%}. "
              f"A false block is a broken gate.")
        ok = False
    if strict["recall"] < MIN_STRICT_RECALL:
        print(f"FAIL: strict recall {strict['recall']:.0%} < {MIN_STRICT_RECALL:.0%}")
        ok = False
    if advise["recall"] < MIN_ADVISE_RECALL:
        print(f"FAIL: advise recall {advise['recall']:.0%} < {MIN_ADVISE_RECALL:.0%}")
        ok = False

    synth = test_synthetic_invariants()
    if synth:
        ok = False
        print(f"FAIL: {len(synth)} synthetic invariant(s) broken:")
        for why, text in synth:
            print(f"   {why}: {text!r}")
    else:
        print("synthetic invariants: all pass")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
