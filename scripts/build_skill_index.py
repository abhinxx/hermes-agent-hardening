#!/usr/bin/env python3
"""Build the keyword index that skill_suggest.py matches against.

    python3 scripts/build_skill_index.py

Reads every SKILL.md under ~/.hermes/skills/, extracts the name, description,
frontmatter tags, and the "When to use" / "When to Use" section, and writes a
keyword index to ~/.hermes/agent-hooks-state/skill_index.json.

Re-run after adding or editing skills. Cheap: no LLM, pure text.
"""

import json
import os
import re
import sys
from typing import Optional

SKILLS_DIR = os.path.expanduser(os.environ.get("HERMES_SKILLS_DIR", "~/.hermes/skills"))
OUT_PATH = os.path.expanduser("~/.hermes/agent-hooks-state/skill_index.json")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "it", "this", "that", "with", "you", "your", "me", "my", "i", "we",
    "do", "does", "did", "be", "been", "can", "could", "would", "should",
    "what", "why", "how", "when", "where", "which", "who", "not", "no", "yes",
    "so", "if", "then", "than", "as", "by", "from", "up", "out", "about",
    "just", "now", "get", "got", "want", "need", "like", "all", "any", "one",
    "use", "using", "used", "user", "agent", "skill", "hermes", "via", "them",
    "each", "per", "into", "over", "under", "more", "most", "some", "only",
}


def tokenize(text: str) -> set:
    words = re.findall(r"[a-z0-9][a-z0-9\-_]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def parse_skill(path):
    # type: (str) -> Optional[dict]
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return None

    name = os.path.basename(os.path.dirname(path))
    description = ""
    tags: list[str] = []

    fm = re.match(r"^---\n(.*?)\n---\n", raw, re.DOTALL)
    body = raw
    if fm:
        block = fm.group(1)
        body = raw[fm.end():]
        m = re.search(r"^name:\s*(.+)$", block, re.M)
        if m:
            name = m.group(1).strip().strip("\"'")
        m = re.search(r"^description:\s*(.+)$", block, re.M)
        if m:
            description = m.group(1).strip().strip("\"'")
        m = re.search(r"tags:\s*\[(.*?)\]", block, re.DOTALL)
        if m:
            tags = [t.strip().strip("\"'").lower() for t in m.group(1).split(",") if t.strip()]

    # "When to use" section carries the trigger language -- the most valuable
    # signal for matching a user's phrasing.
    when = ""
    m = re.search(r"^#{1,4}\s*When to [Uu]se\b(.*?)(?=^#{1,4}\s|\Z)", body, re.DOTALL | re.M)
    if m:
        when = m.group(1)

    keywords = tokenize(name.replace("-", " ")) | tokenize(description) | tokenize(when)
    keywords |= set(t for tag in tags for t in tokenize(tag))
    keywords |= set(p for p in name.lower().split("-") if len(p) > 2 and p not in STOPWORDS)

    return {
        "name": name,
        "description": description,
        "path": path,
        "keywords": sorted(keywords),
    }


def main() -> int:
    if not os.path.isdir(SKILLS_DIR):
        print(f"skills dir not found: {SKILLS_DIR}", file=sys.stderr)
        return 1

    skills = []
    for root, dirs, files in os.walk(SKILLS_DIR):
        # Prune hidden dirs in place (.archive, .curator_backups, .hub).
        # NOTE: must not test the absolute path for "/." -- SKILLS_DIR itself
        # lives under ~/.hermes, which would match and skip everything.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if "SKILL.md" in files:
            entry = parse_skill(os.path.join(root, "SKILL.md"))
            if entry and entry["keywords"]:
                skills.append(entry)

    skills.sort(key=lambda s: s["name"])
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({"skills": skills, "count": len(skills)}, f, indent=1)

    total_kw = sum(len(s["keywords"]) for s in skills)
    print(f"indexed {len(skills)} skills, {total_kw} keywords -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
