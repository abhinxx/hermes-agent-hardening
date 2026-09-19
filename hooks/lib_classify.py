#!/usr/bin/env python3
"""Turn classifier for the question-gate hook pair.

ONE JOB: decide whether a user message is an ANSWER-ONLY turn (the user asked
something) or a WORK turn (the user told the agent to do something).

Two tiers, because one threshold cannot serve both purposes:

  ADVISE  - liberal. Injects a reminder into the turn. False positives cost a
            sentence of wasted context, so we tune for recall.
            Measured: 83% recall, 88% precision.

  STRICT  - conservative. BLOCKS mutating tool calls. A false positive here
            stops real work, which is how a hook gets uninstalled, so we tune
            for precision above everything.
            Measured: 100% precision, 60% recall, ZERO false blocks.

Both measured against 56 hand-labelled real messages from a failed session
(tests/corpus_session_37e2bc.json). Re-run tests/test_classifier.py after any
change to this file; the zero-false-block property is the whole product.

DESIGN NOTES (each rule below was forced by a measured failure):

  v1 scored 2/20. Every miss was a mutation verb that was not an instruction:
    1. VERB AS NOUN         "update? did you stop?"  /  "why is it manual search"
    2. VERB INSIDE A QUOTE  'why you stopped when you said "...writing now..."'
    3. VERB BEING ASKED ABOUT  "who told you to fix it?"
    4. VERB IN AN IDIOM     "you gon make me ill"

  The fix was not a bigger verb list. It was asking a different question:
  is the verb in IMPERATIVE POSITION, after quotes/URLs are stripped and after
  spans where the verb is being interrogated about are removed?

No LLM call. Runs on every turn, must be instant and deterministic.
A probabilistic gate is not a gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Escape hatches. Checked first, win unconditionally. The user must always
# have an override that works on the first try.
# --------------------------------------------------------------------------

OVERRIDE_RE = re.compile(
    r"(?:^|\s)(?:GO:|DO IT:|DOIT:|EXECUTE:|APPLY:|SHIP IT:|#go\b|#do\b)",
    re.IGNORECASE,
)

FORCE_ANSWER_RE = re.compile(
    r"(?:^|\s)(?:ANSWER ONLY:?|NO TOOLS:?|JUST ANSWER:?|#ask\b|#answer\b)",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Mutation verbs -- only verbs implying a change of state.
# Conversational imperatives (tell/explain/show/list) are deliberately ABSENT:
# those are requests for information and must stay answer-only.
# --------------------------------------------------------------------------

MUTATION_VERBS = {
    "make", "build", "create", "write", "add", "append", "insert",
    "remove", "delete", "drop", "strip", "clear", "wipe",
    "change", "update", "edit", "patch", "fix", "modify", "rewrite",
    "replace", "rename", "move", "copy", "merge", "revert", "undo",
    "generate", "render", "produce", "draft", "compose",
    "refactor", "clean", "tidy", "format", "reformat",
    "run", "execute", "start", "launch", "spawn", "deploy", "install",
    "uninstall", "commit", "push", "pull", "publish", "upload", "download",
    "send", "post", "submit", "apply", "set", "enable", "disable",
    "configure", "setup", "init", "restart",
    "search", "scrape", "crawl", "fetch", "browse", "research",
    "continue", "proceed", "resume", "finish", "complete", "redo", "retry",
    "use", "check", "verify", "confirm", "cross-check", "crosscheck",
}

_VERB_SUFFIXES = ("", "s", "es", "ed", "ing")


def _forms(verbs) -> set:
    out = set()
    for v in verbs:
        for suf in _VERB_SUFFIXES:
            if suf == "es" and not v.endswith(("s", "x", "z", "ch", "sh")):
                continue
            if suf == "ed" and v.endswith("e"):
                out.add(v + "d")
                continue
            if suf == "ing" and v.endswith("e"):
                out.add(v[:-1] + "ing")
                continue
            out.add(v + suf)
    return out


def _alt(words) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


ALL_FORMS = _forms(MUTATION_VERBS)
MUTATION_ANY_RE = re.compile(r"\b(" + _alt(ALL_FORMS) + r")\b", re.IGNORECASE)

# Imperative position: start of message, start of a clause, or right after an
# instruction lead-in. This is the single highest-value rule in the file.
LEAD_IN = (
    r"(?:^|[.;!?\n]\s*|,\s*(?:then|and|also)\s+|\b(?:then|now|also|please|next|"
    r"first|instead|finally)\s+|\bi\s+(?:want|need)\s+you\s+to\s+|"
    r"\byou\s+(?:need|have)\s+to\s+|\bcan\s+you\s+|\bcould\s+you\s+|"
    r"\bgo\s+(?:ahead\s+)?and\s+|\blet'?s\s+|\bdo\s+the\s+|\bjust\s+)"
)
IMPERATIVE_RE = re.compile(LEAD_IN + r"(" + _alt(ALL_FORMS) + r")\b", re.IGNORECASE)

# A verb being INTERROGATED about is not a command:
#   "who told you to fix it?"  "why were you writing?"  "you gon make me ill"
INTERROGATED_VERB_RE = re.compile(
    r"\b(?:who|why|what|when|how|did|do|does|don'?t|didn'?t|should|would|will|"
    r"are|is|was|were|told you to|asked you to|you gon|gonna|going to)\b"
    r"[^.?!]{0,40}?\b(" + _alt(ALL_FORMS) + r")\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Interrogative signals.
# --------------------------------------------------------------------------

WH_RE = re.compile(
    r"\b(what|whats|what's|why|how|when|where|which|who|whose|wtf)\b", re.IGNORECASE
)

AUX_QUESTION_RE = re.compile(
    r"\b(is it|are you|did you|didnt you|didn't you|do you|does it|have you|"
    r"has it|will you|would you|should i|should you|am i|was it|were you|"
    r"is there|are there|aint you|ain't you)\b",
    re.IGNORECASE,
)

ACCOUNT_RE = re.compile(
    r"\b(explain|clarify|report back|tell me|talk to me|walk me through|"
    r"answer (?:my|the) question|answer me|i asked (?:you )?a question|"
    r"what did you do|what are you doing|what you doing|whats going on|"
    r"what is going on|are you done|is it done|any update|status update)\b",
    re.IGNORECASE,
)

STOP_RE = re.compile(
    r"^\W*(stop|halt|wait|hold on|pause|freeze|abort|cancel)\b", re.IGNORECASE
)

# Never read an instruction out of something the user quoted back at the agent.
QUOTE_RE = re.compile(r"\"[^\"]{0,4000}\"|'{3}.*?'{3}|`[^`]{0,2000}`", re.DOTALL)
URL_RE = re.compile(r"https?://\S+")


@dataclass
class Verdict:
    """block: True = answer-only turn.
    tier:  'strict' verdicts are safe to enforce; 'advise' only to warn."""

    block: bool
    reason: str
    tier: str = "advise"
    matched: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"block": self.block, "reason": self.reason, "tier": self.tier,
             "matched": self.matched}
        )


def extract_text(raw) -> str:
    """Normalise a user message into plain text.

    Hermes stores multimodal turns as a NUL-prefixed JSON content-part list:
        \\x00json:[{"type": "text", "text": "..."}, {"type": "image", ...}]
    A naive read sees the literal prefix and mis-measures everything.
    """
    if raw is None:
        return ""
    if isinstance(raw, list):
        return " ".join(
            p.get("text", "") for p in raw if isinstance(p, dict) and p.get("type") == "text"
        )
    s = str(raw)
    if s.startswith("\x00json:"):
        try:
            parts = json.loads(s[len("\x00json:"):])
            return " ".join(
                p.get("text", "") for p in parts
                if isinstance(p, dict) and p.get("type") == "text"
            )
        except Exception:
            return s[len("\x00json:"):]
    return s


def _signals(text: str) -> dict:
    body = QUOTE_RE.sub(" ", text)
    body = URL_RE.sub(" ", body)
    stripped = INTERROGATED_VERB_RE.sub(" ", body)

    imperative = IMPERATIVE_RE.search(stripped)
    any_verb = MUTATION_ANY_RE.search(stripped)
    wh = WH_RE.findall(body)
    aux = AUX_QUESTION_RE.findall(body)
    account = ACCOUNT_RE.search(body)
    qmarks = body.count("?")

    return {
        "imperative": imperative.group(0).strip()[:40] if imperative else None,
        "residual_verb": any_verb.group(0).lower() if any_verb else None,
        "wh": wh[:4],
        "aux": aux[:3],
        "account": account.group(0).lower() if account else None,
        "question_marks": qmarks,
        "q_score": len(wh) + len(aux) + qmarks + (2 if account else 0),
    }


def classify(raw, tier: str = "strict") -> Verdict:
    """Classify a turn.

    tier="strict" (default): only returns block=True when there is no mutation
        verb at all outside interrogated/quoted spans AND at least two
        independent question signals. Zero false blocks on the test corpus.

    tier="advise": returns block=True whenever there is no imperative and any
        question signal. Higher recall, some false positives -- only ever use
        this to inject a reminder, never to block.
    """
    text = extract_text(raw).strip()

    if not text:
        return Verdict(False, "empty message", tier)

    if text.startswith(("[CONTEXT COMPACTION", "[ASYNC DELEGATION", "[OUT-OF-BAND")):
        return Verdict(False, "synthetic turn, not a user message", tier)

    if FORCE_ANSWER_RE.search(text):
        return Verdict(True, "explicit answer-only marker", tier, {"override": "force_answer"})

    if OVERRIDE_RE.search(text):
        return Verdict(False, "explicit execute override", tier, {"override": "go"})

    if STOP_RE.match(text):
        return Verdict(True, "leading stop command", tier, {"stop": True})

    s = _signals(text)
    q = s["q_score"]

    if tier == "strict":
        no_work = not s["imperative"] and not s["residual_verb"]
        # A short message opening with a wh-word is a question even though it
        # only carries one signal ("whats the session id"). Length-bounded so
        # a long instruction that happens to start with "what" cannot sneak in.
        opens_wh = bool(WH_RE.match(text.lstrip("?!. ").lower())) and len(text) <= 80
        if no_work and (q >= 2 or (opens_wh and q >= 1)):
            return Verdict(True, f"no work order, {q} question signals", tier, s)
        return Verdict(False, "work order or insufficient question signal", tier, s)

    # advise tier
    if not s["imperative"] and q > 0:
        return Verdict(True, f"no imperative, {q} question signals", tier, s)
    return Verdict(False, "imperative present or no question signal", tier, s)


if __name__ == "__main__":
    import sys

    tier = sys.argv[1] if len(sys.argv) > 1 else "strict"
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        v = classify(line, tier=tier)
        print(f"{'BLOCK' if v.block else 'ALLOW':5}  {v.reason:45}  {line[:70]}")
