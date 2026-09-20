# Hermes Agent Hardening

**Deterministic enforcement for agent behaviour. Derived from one measured
failure, tested against the real messages that caused it.**

A 3h25m Hermes session went wrong in twelve distinct ways. This repo is the
result of querying the session database instead of guessing, hand-labelling
the messages, and building only the controls that can actually be enforced.

Not a philosophy. Seven hooks, a classifier, and a test suite that fails the
build if the classifier ever blocks a real instruction.

---

## The incident in four numbers

| | |
|---|---|
| **20** | times the agent fired a tool when the user had asked a question or said stop |
| **60** | narration messages ("Writing it now.") that carried tool calls and no result |
| **3** | skill loads across 219 API calls, every one of them after the user shouted for it |
| **1,009s** | worst case from a user question to a substantive answer |

Every number from SQL against `~/.hermes/state.db`. The user's own post-mortem
said "three" mode violations. The database said twenty.

Full analysis: **[PLAN.md](PLAN.md)**.

---

## What it installs

| Control | Event | Prevents | Enforced |
|---|---|---|---|
| **question-gate** | `pre_llm_call` + `pre_tool_call` | Acting when you asked a question | **Yes** - exit 2 |
| **no-full-rewrite** | `pre_tool_call` | 51KB rewrites to change a colour | **Yes** - exit 2 |
| **browser-lock** | `pre_tool_call` | Parallel subagents racing one browser | **Yes** - fail_closed |
| **persist-subagent** | `subagent_stop` | Child output lost in a transient cache | **Yes** - observer |
| **skill-suggest** | `pre_llm_call` | Improvising when a skill exists | Advisory |
| **SOUL.md** | system prompt | Over-building, narration, mode drift, style resets | Prompt-level |
| **config flips** | config | Unverified "done", runaway loops | **Yes** - built-in |

### Why SOUL.md and not a context-injection hook

Ponytail and similar tools inject their ruleset through a `pre_llm_call` hook.
On Hermes that is the wrong slot: hook context is appended to the **user
message**, never the system prompt, specifically to protect the prompt cache.
A 6KB ruleset injected that way is re-sent as fresh uncached tokens on every
single turn.

`SOUL.md` is loaded once into the cached system prompt and costs effectively
nothing after the first turn. Same behaviour, no recurring token bill. The
hooks are reserved for the things only a hook can do: refusing a tool call.

### The ruleset

`soul/SOUL.md` covers, in order: do the least thing that achieves the objective
(a 5-rung ladder, search before building, match effort to the task), lazy about
the solution but never about understanding, ask/plan/execute modes, no
narration, report in artifacts, patch never regenerate, style instructions are
permanent, structure by the user's model, load the documented method, delegate
genuinely parallel work (and only that), own what you delegate, honesty, tone.

---

## The hard part: blocking without breaking

A `pre_tool_call` hook can refuse a tool but **cannot see the user's message**.
A `pre_llm_call` hook sees the message but **cannot refuse anything**.

Pairing them through a shared state file is what makes real enforcement
possible: the first classifies and records, the second reads the verdict and
exits 2.

That only works if the classifier never gets it wrong in the expensive
direction.

### Measured on 56 hand-labelled real messages

| Tier | Purpose | Accuracy | Precision | Recall | False blocks |
|---|---|---|---|---|---|
| **STRICT** | **blocks tools** | 79% | **100%** | 66% | **0** |
| ADVISE | injects a reminder | 82% | 88% | 83% | never blocks |

The strict tier deliberately misses a third of genuine questions rather than
risk stopping one real instruction. A missed question costs a wasted reminder.
A false block costs the whole system's credibility.

`tests/test_classifier.py` **fails the build if precision drops below 100%.**

### Why a naive version does not work

v1 scored 2/20. Every miss was a mutation verb that was not an instruction:

```
"update? did you stop?"                          verb as a noun
"why you stopped when you said '...writing...'"  verb inside a quote
"who told you to fix it?"                        verb being interrogated about
"you gon make me ill"                            verb in an idiom
```

The fix is not a longer verb list. It is checking whether the verb sits in
**imperative position** after quotes, URLs, and interrogated spans are stripped.

### Escape hatches

```
GO: rewrite the whole thing        # never arms the gate
DO IT: fix it                      # same
ANSWER ONLY: what is your plan     # forces answer-only
```

Read-only tools (`read_file`, `web_search`, `session_search`, ...) are never
blocked. No state file means no block: fail-open by design.

---

## Install

On any machine that already has Hermes:

```bash
curl -fsSL https://raw.githubusercontent.com/abhinxx/hermes-agent-hardening/main/bootstrap.sh | bash
```

Clones to `~/.hermes/hardening`, runs the tests, installs, grants hook consent.
Idempotent - re-run it to update.

Manual equivalent:

```bash
git clone https://github.com/abhinxx/hermes-agent-hardening
cd hermes-agent-hardening

python3 tests/test_classifier.py     # must print PASS
bash tests/test_hooks.sh             # must print: passed: 22   failed: 0

bash scripts/install.sh              # dry run - prints every change
bash scripts/install.sh --apply      # commits, with timestamped backups

hermes --accept-hooks -z "ok"        # REQUIRED: hooks do not fire unapproved
hermes hooks doctor                  # must say "All shell hooks look healthy"
```

**Then restart Hermes, the desktop app, and the gateway.** Hooks register at
process start; anything already running will not have them.

Rollback at any time:

```bash
bash scripts/uninstall.sh --apply
```

---

## Layout

```
PLAN.md                      full analysis, architecture, phases, open questions
hooks/
  lib_classify.py            two-tier turn classifier (the core)
  question_gate_pre_llm.py   classify turn, arm state, inject reminder
  question_gate_pre_tool.py  block mutating tools on an answer-only turn
  no_full_rewrite.py         refuse whole-file rewrites of large files
  browser_lock.py            single-owner browser lock
  persist_subagent.py        durable subagent output
  skill_suggest.py           keyword-match skills against the user's message
scripts/
  install.sh                 dry-run by default, --apply to commit
  uninstall.sh               restores backups
  build_skill_index.py       index SKILL.md files into keywords
  merge_hooks_config.py      append hooks block without a YAML dependency
  session_metrics.py         re-measure any session from the DB
soul/SOUL.md                 standing operating discipline
config/hooks.yaml            the hooks block to merge
tests/
  test_classifier.py         56 labelled messages, enforces 100% precision
  test_hooks.sh              22 end-to-end hook tests
  corpus_session_37e2bc.json the labelled corpus
evidence/                    baseline metrics from the failed session
```

---

## Honest limits

**4 of 12 symptoms become mechanically impossible.** 2 more get materially
harder. The rest are prompt-level and only ever get less frequent.

Specifically unfixable:

- **"Should have loaded a skill"** - no event fires on this. `skill_suggest`
  injects candidates but cannot force a load, and forcing would be worse.
- **"Should have known that was rhetorical"** - the strict tier catches 66%.
  The rest needs intent reading a regex cannot do.

Anyone promising deterministic prevention for an intent-reading problem is
repeating the overclaiming that caused the incident in the first place.

---

## Verify it worked

Re-measure after two weeks of real use:

```bash
python3 scripts/session_metrics.py <session_id>
```

| Metric | Baseline | Target |
|---|---|---|
| Mode violations per session | 20 | 0-2 |
| Narration-then-tool messages | 60 | < 10 |
| Skill loads per 100 API calls | 1.4 | > 5 |
| Full-file rewrites of large files | 3 | 0 |
| p90 latency to substantive answer | 245s | < 90s |

If these do not move, the hooks are theatre and should be removed.

---

## Requirements

- Hermes Agent with shell-hook support (`hermes hooks --help`)
- `python3` 3.9+ (no third-party packages; macOS system Python works)

## Related

- [NousResearch/hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution) -
  evolves skill *text* with DSPy + GEPA. Complementary: that project improves
  what the instructions say, this one enforces that they are followed.

## License

MIT
