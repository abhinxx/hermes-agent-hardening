# PLAN.md - Hermes Agent Hardening

**Deterministic enforcement for agent behaviour, derived from one measured failure.**

This is the full plan: what broke, what the numbers say, what is mechanically
preventable, what is not, and the exact order of operations to install it.

---

## 1. The incident

Session `20260917_143455_37e2bc`. A research-and-build task: compare university
modules from multiple sources and produce one HTML page.

Every number below comes from a SQL query against `~/.hermes/state.db`. None of
it is reconstructed from a transcript or a summary.

| Metric | Value |
|---|---|
| Working window | 11:35 - 15:00 = **3h 25m** |
| User messages in window | **61** |
| API calls | 178 main + 41 background = **219** |
| Output tokens | 232,362 |
| Cache read / write | 45.6M / 7.9M |
| Reasoning effort | **xhigh** |
| Skill loads | **3** |
| Context compactions | 1 |

Tool census: `browser_exec` 40, `terminal` 25, `patch` 24, `web_search` 19,
`delegate_task` 17, `web_extract` 11, `write_file` 6, `skill_view` **3**,
`read_file` 2.

Three skill loads in 219 API calls, at xhigh reasoning. This was not a
thinking-budget problem. The agent had the tools, the data, the skills, and the
compute.

### The three measurements that matter

**20 mode violations.** For every user message matching a question or stop
pattern, we checked whether the agent's next message carried `tool_calls`. It
did, 20 times. The user's own post-mortem doc said "three times". The database
said twenty. A post-mortem built on impressions undercounts by 6x.

**60 narration-then-tool messages.** Assistant messages under 90 characters
that carry tool calls. "Writing it now." "Got everything. Building the page."
"Writing now - 60 seconds." Sixty placeholders standing where progress should
have been.

**Latency to a substantive answer** (user message to next assistant message
with no tool calls and >250 chars): median 41s, p90 245s, **max 1,009s**.
Nearly 17 minutes from a question to an answer.

### The escalation curve

| Window | Msgs | Profanity | Avg CAPS | Avg chars |
|---|---|---|---|---|
| 11:30 | 3 | 11 | 0.38 | long |
| 12:30 | 2 | 1 | 0.00 | 63 |
| **13:00** | **25** | **62** | 0.20 | 473 |
| 13:30 | 8 | 18 | **0.38** | 253 |
| 14:00 | 8 | 11 | **0.01** | 534 |
| 14:30 | 14 | 31 | 0.07 | long |

Read the 14:00 row. CAPS collapses from 38% to 1% while message length nearly
doubles. That is not the user calming down. That is the user giving up on
getting the agent to respond to pressure and starting to write the
specification the agent should have derived itself.

---

## 2. Twelve symptoms, five root causes

| Cause | Symptoms |
|---|---|
| **A. No output-versus-instruction check** | Light mode ignored 4x; wrong grouping; 5 of 12 rows; stale cross-references |
| **B. Wrong tool granularity** | 3 interrupted 51KB `write_file` rewrites to change a colour; no wall-clock estimate before long silent calls |
| **C. Ask/plan/execute collapsed** | 20 mode violations; questions treated as preambles to action |
| **D. Improvised over documented** | 3 skill loads in 219 calls; 3 attempts at one Reddit technique then "impossible" |
| **E. Delegated without owning** | 9 children, 3 broke the browser constraint, 1 made 52 browser calls and 0 screenshots, output left in a transient cache |

**Underneath all five:** the agent optimised for looking busy in the transcript
rather than producing bytes on disk. The 60 narration messages are the proof.

---

## 3. The deepest root cause: retrieval, not knowledge

This is the finding that changes what to build.

Hermes already has a self-improvement system. A background fork runs roughly
every 10 turns and writes skills and memory. During this very session it spent
**41 API calls and 60,672 output tokens** and produced exactly one artifact: a
skill named `audited-research-deliverables`, created at **14:31:58**.

The damage window was 11:35 to 14:33. The skill landed at **minute 176 of a
205-minute disaster**, and was never loaded during the work.

Its content is genuinely good. It names the failures precisely: obey style
instructions permanently including in new files, group by the user's mental
model, patch incrementally, answer questions before resuming work, exactly one
agent may use the browser.

Now look at when skills were actually loaded, from `.usage.json`:

| Skill | Loaded at | What the user said |
|---|---|---|
| `reddit-ai-research` | 13:27:30 | 13:27:24 - "WHY DONT YOU FUCKING USE REDDIT SEARCH SKILL" |
| `linkup-search` | 14:41:35 | 14:41:26 - "use linkup skill" |

Six seconds. Nine seconds. **Every skill load in the session happened because
the user shouted for it.**

The loop is: agent fails -> background fork writes an excellent skill about the
failure -> skill sits unloaded -> agent fails the same way -> user shouts ->
skill loads.

Knowledge accumulates. Retrieval never fires. Writing better skills does not
fix this. The trigger is what is broken, and `skill_suggest.py` exists to fix
exactly that.

---

## 4. Enforcement surfaces: what is real

Checked against the live install before designing anything.

| Surface | Can it block? | Sees the user message? |
|---|---|---|
| `pre_tool_call` shell hook | **Yes** (exit 2) | No - only `tool_name`, `tool_input`, `session_id`, `cwd` |
| `pre_llm_call` shell hook | No | **Yes** - `extra.user_message` |
| `subagent_stop` shell hook | No (observer) | n/a - gets `child_summary` |
| `pre_verify` hook | Keeps a turn alive | n/a |
| `SOUL.md` | No (prompt-level) | n/a |
| Memory | No (prompt-level) | n/a |

**The key insight.** A tool-blocking hook cannot see what the user said, and
the hook that can see the user's message cannot block. Neither is sufficient
alone. Pairing them through a shared state file produces real enforcement:
`pre_llm_call` classifies and records, `pre_tool_call` reads the verdict and
refuses.

### Starting state of this install

Everything was factory-default:

- `~/.hermes/hooks/` - **empty**
- `hooks:` in config.yaml - **absent**
- `~/.hermes/plugins/` - **empty**
- `~/.hermes/SOUL.md` - **stock 1-line default, never edited**
- `MEMORY.md` - **2,200 / 2,200 chars, completely full**
- `USER.md` - **1,374 / 1,375 chars, completely full**
- `agent.verify_on_stop` - **false**
- `tool_loop_guardrails.hard_stop_enabled` - **false**
- curator - 4 runs, `consolidate: off`, 0 changes ever

Every behavioural instruction lived in two files that were 100% full. The one
global always-loaded slot built for standing rules was unused.

Note: `~/AGENTS.md` is **not** a global rules file. Outside a git repo Hermes
reads `AGENTS.md` only from the exact working directory, so one in the home
directory affects only sessions started there. `SOUL.md` is the global slot.

---

## 5. Architecture

```
                 user message
                      |
                      v
        +-----------------------------+
        |  pre_llm_call               |
        |                             |
        |  question_gate_pre_llm.py   |---> writes verdict to
        |    classify(strict|advise)  |     agent-hooks-state/
        |    inject reminder          |     <session>.turn.json
        |                             |
        |  skill_suggest.py           |---> injects "consider loading X"
        +-----------------------------+
                      |
                      v
                 model decides
                      |
                      v
        +-----------------------------+
        |  pre_tool_call              |
        |                             |     reads verdict
        |  question_gate_pre_tool.py  |<----------+
        |    mutating tool + armed    |
        |      -> exit 2 BLOCK        |
        |                             |
        |  no_full_rewrite.py         |
        |    write_file + exists      |
        |      + >15KB -> exit 2      |
        |                             |
        |  browser_lock.py            |
        |    other session holds lock |
        |      -> exit 2 (fail_closed)|
        +-----------------------------+
                      |
                      v
                 tool executes
                      |
                      v
        +-----------------------------+
        |  subagent_stop              |
        |  persist_subagent.py        |---> ~/hermes_runs/<session>/
        +-----------------------------+
```

---

## 6. The question-gate classifier

The hardest part, because a gate that blocks real instructions gets
uninstalled on day one.

### Why v1 failed

v1 used a simple rule: mutation verb present means work order, otherwise
question. It scored **2 of 20** known violations. Every miss had the same
shape: a mutation verb that was not an instruction.

1. **Verb as noun** - "update? did you stop?" / "why is it manual search"
2. **Verb inside a quote** - `why you stopped when you said "...writing now..."`
3. **Verb being interrogated about** - "who told you to fix it?"
4. **Verb in an idiom** - "you gon make me ill"

The fix was not a longer verb list. It was a different question: is the verb in
**imperative position**, after quoted spans and URLs are stripped, and after
spans where the verb is the object of a question are removed?

### Two tiers

A single threshold cannot serve both jobs, so there are two.

| Tier | Used for | Accuracy | Precision | Recall | False blocks |
|---|---|---|---|---|---|
| **STRICT** | **Blocking tools** | 79% | **100%** | 66% | **0** |
| ADVISE | Injecting a reminder | 82% | 88% | 83% | n/a (never blocks) |

Measured against **56 hand-labelled real messages** from the failed session
(`tests/corpus_session_37e2bc.json`): 35 questions, 21 work orders.

**The strict tier has never produced a false block on the corpus.** It
deliberately misses about a third of genuine questions rather than risk
stopping one real instruction. A missed question costs one wasted reminder. A
false block costs the user's trust in the entire system.

This asymmetry is the single most important design decision in the repo, and
`tests/test_classifier.py` fails the build if precision drops below 100%.

### Escape hatches

- `GO:` / `DO IT:` / `EXECUTE:` anywhere in a message - never arms the gate
- `ANSWER ONLY:` / `NO TOOLS:` - forces answer-only even on an imperative
- Read-only tools are never blocked, so the agent can still look things up
- No state file means no block. Fail-open by design.

---

## 7. What each control prevents

| # | Control | Event | Kills | Enforced? |
|---|---|---|---|---|
| 1 | question-gate | `pre_llm_call` + `pre_tool_call` | C (20 violations) | **Yes**, exit 2 |
| 2 | no-full-rewrite | `pre_tool_call` | B (3 dead writes) | **Yes**, exit 2 |
| 3 | browser-lock | `pre_tool_call` | E (browser race) | **Yes**, fail_closed |
| 4 | persist-subagent | `subagent_stop` | E (lost output) | **Yes**, observer |
| 5 | skill-suggest | `pre_llm_call` | D (retrieval) | Partly, advisory |
| 6 | SOUL.md | prompt | A, C, narration | Partly, prompt-level |
| 7 | config flips | config | A (verify), loops | **Yes**, built-in |

**Honest split: 4 of 12 symptoms become mechanically impossible. 2 more get
materially harder. The rest are prompt-level and only ever get less frequent.**

Anyone promising deterministic prevention for an intent-reading problem is
repeating the overclaiming that caused the incident.

### What stays unfixable

- **"Should have loaded a skill"** - no event fires on this. `skill_suggest`
  attacks it sideways by injecting candidates, but cannot force a load, and
  forcing would be worse: the agent would load irrelevant skills and burn
  context.
- **"Should have known that was rhetorical"** - the strict tier catches 66%.
  The remaining third needs intent reading that a regex cannot do.

---

## 8. Phases

### Phase 0 - Evidence (done, in this repo)
Query the DB, quantify, hand-label a corpus. Nothing is designed before this.

### Phase 1 - Deterministic hooks (ready)
The four blocking/observing hooks. Every one tested against synthetic payloads
matching the documented wire protocol. 22 hook tests, all passing.

### Phase 2 - Config flips (ready, one command each)
```
agent.verify_on_stop                      false -> true
tool_loop_guardrails.hard_stop_enabled    false -> true
delegation.max_concurrent_children        10    -> 4
```

### Phase 3 - SOUL.md (ready)
Standing behavioural rules in the global always-loaded slot. No character cap,
unlike memory. Relieves the two 100%-full memory files.

**Slot choice matters.** Hook-injected context is appended to the *user
message*, never the system prompt, deliberately, to keep the prompt cache
intact. A ruleset injected that way is re-sent uncached every turn. `SOUL.md`
is loaded once into the cached system prompt. Same behaviour, no recurring
cost. Hooks stay reserved for what only a hook can do: refuse a tool call.

Adapted from [ponytail](https://github.com/DietrichGebert/ponytail), whose
measured result is -54% LOC on real agentic tasks:

- **The ladder.** Stop at the first rung that holds: does it need to exist,
  does it already exist here, does the stdlib/platform cover it, can it be one
  line, only then write the minimum. Generalised past code to documents,
  research, and plans.
- **Lazy about the solution, never about understanding.** Never simplify away
  verification, trust-boundary validation, data-loss handling, security, or
  anything explicitly requested. Small because sufficient, not because cut short.
- **The debt marker.** A `shortcut:` comment naming the ceiling and the upgrade
  path, so a deliberate compromise is greppable instead of silent.

Not adopted: their per-turn injection hook (wrong slot on Hermes, see above),
and the lite/full/ultra mode dial (a second control surface for a single user
who wants one behaviour).

### Phase 3b - Delegation (ready)

The inverse problem to over-building: the agent almost never spawns subagents,
even when a task is embarrassingly parallel. `SOUL.md` now states the trigger
explicitly - three or more pieces that do not depend on each other, each
describable in a short brief - and the anti-trigger: not for a single lookup,
not when step two needs step one, not to look busy. Capped at four, matching
`delegation.max_concurrent_children`.

This is prompt-level. There is no event that fires on "should have delegated",
so it cannot be enforced. If it does not move in Phase 5, the next lever is a
`pre_llm_call` hook that detects list-shaped requests and injects a reminder.

### Phase 4 - Skill retrieval (ready)
`build_skill_index.py` + `skill_suggest.py`. Indexes 105 skills into 3,042
keywords. Verified to surface `reddit-ai-research` and `linkup-search` for the
exact messages where the user had to shout for them.

### Phase 5 - Measure again (after 2 weeks of real use)
Re-run `scripts/session_metrics.py` on new sessions and compare against the
baseline in `evidence/`. The metrics that must move:

| Metric | Baseline | Target |
|---|---|---|
| Mode violations per session | 20 | 0-2 |
| Narration-then-tool messages | 60 | < 10 |
| Skill loads per 100 API calls | 1.4 | > 5 |
| Full-file rewrites of large files | 3 | 0 |
| p90 latency to substantive answer | 245s | < 90s |
| Subagents spawned on parallel tasks | ~0 unprompted | used when 3+ independent pieces |

If these do not move, the hooks are theatre and should be removed.

---

## 9. Install order

Order matters. Hooks before config, config before flips, test before all.

```bash
git clone https://github.com/abhinxx/hermes-agent-hardening
cd hermes-agent-hardening

python3 tests/test_classifier.py    # must print PASS
bash tests/test_hooks.sh            # must print 22 passed

bash scripts/install.sh             # dry run, prints every change
bash scripts/install.sh --apply     # commits, with timestamped backups
```

The installer refuses to run if either test suite fails.

Rollback: `bash scripts/uninstall.sh --apply`.

---

## 10. Design constraints

1. **No false blocks.** Precision above recall, always, on anything that can
   refuse a tool call.
2. **Fail open by default.** These are discipline gates, not security
   boundaries. A crashed hook must never stop real work. The one exception is
   `browser_lock`, where a race costs more than a refused call.
3. **No LLM calls in hooks.** They run on every turn. Instant and
   deterministic, or they are not gates.
4. **Python 3.9 compatible.** Hooks run under whichever `python3` is on PATH.
   On macOS that is `/usr/bin/python3` = 3.9.6, which has no PyYAML and no
   `X | None` syntax.
5. **Every change reversible.** Timestamped backups, a real uninstaller.
6. **Every number from a query actually run.** No estimates anywhere in this
   document.

---

## 11. Open questions

1. **Should the strict tier get more aggressive over time?** It currently
   misses a third of questions. Raising recall risks the zero-false-block
   property. Recommendation: leave it until Phase 5 data exists.

2. **Should `skill_suggest` ever force a load?** Recommendation: no. Removing
   the excuse is the goal, not removing the choice.

3. **Do the two 100%-full memory files need pruning?** Once SOUL.md carries
   standing rules, several memory entries become redundant. Recommendation:
   audit after Phase 3 lands.

4. **Should the curator's consolidation pass be turned on?** It has run 4 times
   and changed nothing. Either enable consolidation or stop running it.
