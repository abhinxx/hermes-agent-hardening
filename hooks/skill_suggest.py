#!/usr/bin/env python3
"""pre_llm_call hook -- suggest relevant skills based on the user's message.

THE ROOT CAUSE THIS ATTACKS
  In the session that motivated this repo, the agent made 219 API calls and
  loaded 3 skills. Every skill load it did perform happened seconds after the
  user shouted for it:

      13:27:24  user: "WHY DONT YOU FUCKING USE REDDIT SEARCH SKILL"
      13:27:30  agent loads reddit-ai-research        (+6s)

      14:41:26  user: "use linkup skill"
      14:41:35  agent loads linkup-search             (+9s)

  Meanwhile the background self-improvement fork spent 41 API calls and 60k
  tokens writing an excellent skill about the exact failures in progress. It
  landed at minute 176 of a 205-minute session and was never loaded.

  That is the real defect: knowledge accumulates, retrieval never fires.
  Writing better skills does not fix it. The trigger is what is broken.

WHAT THIS DOES
  Keyword-matches the user's message against the skill index and injects a
  short "consider loading X" line. Mechanical, fires every turn, costs one
  line of context, does not depend on the agent noticing anything.

  It does NOT force a load. Forcing would be worse: the agent would load
  irrelevant skills and burn context. It removes the excuse, not the choice.

INDEX
  Built by scripts/build_skill_index.py into
  ~/.hermes/agent-hooks-state/skill_index.json. Re-run it after adding skills.

stdin:  {"hook_event_name": "pre_llm_call", "extra": {"user_message": "..."}}
stdout: {} | {"context": "..."}
"""

import json
import os
import re
import sys

INDEX_PATH = os.path.expanduser("~/.hermes/agent-hooks-state/skill_index.json")
MAX_SUGGESTIONS = 3
MIN_SCORE = 2

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "it", "this", "that", "with", "you", "your", "me", "my", "i", "we",
    "do", "does", "did", "be", "been", "can", "could", "would", "should",
    "what", "why", "how", "when", "where", "which", "who", "not", "no", "yes",
    "so", "if", "then", "than", "as", "by", "from", "up", "out", "about",
    "just", "now", "get", "got", "want", "need", "like", "all", "any", "one",
}


def tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9][a-z0-9\-_]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    msg = (payload.get("extra") or {}).get("user_message") or ""
    if not msg or len(msg) < 12:
        print("{}")
        return 0

    try:
        with open(INDEX_PATH) as f:
            index = json.load(f)
    except Exception:
        print("{}")
        return 0

    msg_tokens = tokens(msg)
    if not msg_tokens:
        print("{}")
        return 0

    scored = []
    msg_lower = msg.lower()
    for entry in index.get("skills", []):
        kw = set(entry.get("keywords", []))
        if not kw:
            continue
        overlap = msg_tokens & kw
        name = entry["name"]
        name_l = name.lower()

        score = len(overlap)

        # Full name mentioned outright ("use the browser-harness skill").
        if name_l in msg_lower:
            score += 5

        # A distinctive part of the name mentioned ("reddit", "linkup").
        # These are the cases where the user is explicitly naming the tool they
        # want, which is the strongest possible signal and must not be lost to
        # a two-keyword minimum.
        name_parts = [p for p in name_l.split("-") if len(p) > 3]
        hit_parts = [p for p in name_parts if p in msg_tokens]
        if hit_parts:
            score += 3 * len(hit_parts)
            overlap |= set(hit_parts)

        if score >= MIN_SCORE:
            scored.append((score, name, entry.get("description", ""), sorted(overlap)[:4]))

    if not scored:
        print("{}")
        return 0

    scored.sort(reverse=True)
    top = scored[:MAX_SUGGESTIONS]

    lines = ["RELEVANT SKILLS (matched against your own skill index):"]
    for score, name, desc, hits in top:
        lines.append(f"  - {name}: {desc[:80]} [matched: {', '.join(hits)}]")
    lines.append(
        "Load any that apply with skill_view(name=...) BEFORE improvising. "
        "The documented method beats what you would invent."
    )

    print(json.dumps({"context": "\n".join(lines)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
