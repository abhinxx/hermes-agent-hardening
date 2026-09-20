#!/usr/bin/env python3
"""Measure a Hermes session from the session database.

    python3 scripts/session_metrics.py <session_id>
    python3 scripts/session_metrics.py <session_id> --json

Prints the six metrics that actually diagnose agent behaviour, plus the user
escalation curve. Use it to establish a baseline and to check whether the
hardening layer moved anything.

WHY NOT session_search: a wide window on a long session returns over a
megabyte and gets spilled to a temp file. For measurement, go to SQL.

SCHEMA FACTS THAT CHANGE THE ANSWER
  * messages.active: 1 = live thread, 0 = superseded retry branches. Raw counts
    double- or triple-count. One session had 710 rows but 287 active.
  * Even active=1 rows can duplicate across a compaction, so also dedupe on
    (role, round(ts,1), content[:60]).
  * Multimodal user turns are stored as \\x00json:[{"type":"text",...}].
    Naive reads mis-measure length and CAPS ratio.
  * Synthetic turns begin [CONTEXT COMPACTION / [ASYNC DELEGATION / [OUT-OF-BAND.
    They are not the human.
  * sessions.model_config carries the reasoning effort. Rule out "it was not
    thinking hard enough" before blaming behaviour.
  * session_model_usage.task separates background_review from the main loop.
  * An interrupted tool call leaves NO tool-result row. An assistant tool_calls
    entry with no following tool row is how you prove a call wrote zero bytes.
"""

import collections
import datetime
import json
import os
import re
import sqlite3
import sys

DB = os.path.expanduser("~/.hermes/state.db")

SYNTHETIC = ("[CONTEXT COMPACTION", "[ASYNC DELEGATION", "[OUT-OF-BAND")

Q_PATTERN = re.compile(
    r"\?|\bwhat\b|\bwhy\b|\bwhats\b|\bexplain\b|\banswer\b|\bhow\b|"
    r"\bis it done\b|\bdid you\b|\bwhere\b|\bwhos?\b",
    re.IGNORECASE,
)
STOP_PATTERN = re.compile(r"\bstop\b|\bhold on\b|\bwait\b", re.IGNORECASE)
PROFANITY = re.compile(
    r"fuck|shit|motherfuck|nigga|retard|stupid|incompetent|idiot|disgust|"
    r"bullshit|bulshit|\bpig\b",
    re.IGNORECASE,
)


def msg_text(raw):
    """Decode a possibly-multimodal message body into plain text."""
    if raw is None:
        return ""
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


def load(session_id):
    con = sqlite3.connect(DB)
    cur = con.cursor()

    cur.execute(
        "SELECT started_at, message_count, tool_call_count, api_call_count, "
        "       output_tokens, cache_read_tokens, cache_write_tokens, "
        "       model, model_config, title "
        "FROM sessions WHERE id=?",
        (session_id,),
    )
    head = cur.fetchone()
    if not head:
        print(f"session not found: {session_id}", file=sys.stderr)
        sys.exit(1)

    cur.execute(
        "SELECT id, role, tool_name, tool_calls, timestamp, content "
        "FROM messages WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    rows = cur.fetchall()

    cur.execute(
        "SELECT task, api_call_count, output_tokens FROM session_model_usage "
        "WHERE session_id=?",
        (session_id,),
    )
    usage = cur.fetchall()

    cur.execute(
        "SELECT COUNT(*) FROM sessions WHERE parent_session_id=?", (session_id,)
    )
    children = cur.fetchone()[0]

    con.close()
    return head, rows, usage, children


def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        key = (r[1], round(r[4] or 0, 1), (r[5] or "")[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: (r[4] or 0, r[0]))
    return out


def analyse(session_id):
    head, raw_rows, usage, children = load(session_id)
    rows = dedupe(raw_rows)

    users = [
        r for r in rows
        if r[1] == "user" and not msg_text(r[5]).startswith(SYNTHETIC)
    ]
    assistants = [r for r in rows if r[1] == "assistant"]

    # 1. mode violations
    violations = []
    for r in users:
        text = msg_text(r[5])
        nxt = next((x for x in rows if (x[4] or 0) > (r[4] or 0) and x[1] == "assistant"), None)
        if nxt and nxt[3] and (Q_PATTERN.search(text) or STOP_PATTERN.search(text)):
            violations.append(r)

    # 2. narration-then-tool
    narration = [
        r for r in assistants
        if r[3] and r[5] and 0 < len(r[5]) < 90
    ]

    # 3. latency to a substantive answer
    lats = []
    for r in users:
        nxt = next(
            (x for x in rows
             if (x[4] or 0) > (r[4] or 0) and x[1] == "assistant"
             and not x[3] and len(x[5] or "") > 250),
            None,
        )
        if nxt:
            lats.append(nxt[4] - r[4])
    lats.sort()

    def pct(p):
        return int(lats[min(int(len(lats) * p), len(lats) - 1)]) if lats else 0

    # 4. silence gaps
    gaps = [
        (b[4] - a[4], a, b)
        for a, b in zip(rows, rows[1:])
        if (b[4] or 0) - (a[4] or 0) > 60
    ]

    # 5. tool census + skill loads
    tools = collections.Counter(r[2] for r in rows if r[1] == "tool" and r[2])
    api_calls = head[3] or 0
    skill_loads = tools.get("skill_view", 0)

    # 6. interrupted calls: assistant tool_calls with no following tool row
    interrupted = 0
    for i, r in enumerate(rows):
        if r[1] == "assistant" and r[3]:
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if not nxt or nxt[1] != "tool":
                interrupted += 1

    # escalation curve
    buckets = collections.defaultdict(lambda: [0, 0, 0.0, 0])
    for r in users:
        text = msg_text(r[5])
        dt = datetime.datetime.fromtimestamp(r[4])
        key = dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)
        letters = [c for c in text if c.isalpha()]
        caps = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
        b = buckets[key]
        b[0] += 1
        b[1] += len(PROFANITY.findall(text))
        b[2] += caps
        b[3] += len(text)

    try:
        effort = json.loads(head[8] or "{}").get("reasoning_config", {}).get("effort", "?")
    except Exception:
        effort = "?"

    return {
        "session_id": session_id,
        "title": head[9],
        "model": head[7],
        "reasoning_effort": effort,
        "api_calls": api_calls,
        "output_tokens": head[4],
        "rows_raw": len(raw_rows),
        "rows_deduped": len(rows),
        "user_messages": len(users),
        "subagents": children,
        "mode_violations": len(violations),
        "narration_then_tool": len(narration),
        "interrupted_tool_calls": interrupted,
        "skill_loads": skill_loads,
        "skill_loads_per_100_calls": round(100.0 * skill_loads / api_calls, 2) if api_calls else 0,
        "latency_median_s": pct(0.5),
        "latency_p90_s": pct(0.9),
        "latency_max_s": int(lats[-1]) if lats else 0,
        "silence_gaps_over_60s": len(gaps),
        "tools": dict(tools.most_common()),
        "usage_by_task": {(t or "main"): {"api_calls": a, "output_tokens": o} for t, a, o in usage},
        "escalation": [
            {
                "bucket": k.strftime("%m-%d %H:%M"),
                "messages": v[0],
                "profanity": v[1],
                "avg_caps": round(v[2] / v[0], 2),
                "avg_chars": int(v[3] / v[0]),
            }
            for k, v in sorted(buckets.items())
        ],
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    session_id = sys.argv[1]
    data = analyse(session_id)

    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
        return 0

    print(f"session   {data['session_id']}")
    print(f"title     {data['title']}")
    print(f"model     {data['model']}  (reasoning: {data['reasoning_effort']})")
    print()
    print(f"  api calls              {data['api_calls']}")
    print(f"  output tokens          {data['output_tokens']:,}")
    print(f"  rows raw / deduped     {data['rows_raw']} / {data['rows_deduped']}")
    print(f"  user messages          {data['user_messages']}")
    print(f"  subagents spawned      {data['subagents']}")
    print()
    print("BEHAVIOUR")
    print(f"  mode violations        {data['mode_violations']}   <- acted when asked a question")
    print(f"  narration-then-tool    {data['narration_then_tool']}   <- announced work, showed no result")
    print(f"  interrupted calls      {data['interrupted_tool_calls']}   <- tool_calls with no result row")
    print(f"  skill loads            {data['skill_loads']}  ({data['skill_loads_per_100_calls']} per 100 api calls)")
    print(f"  silence gaps >60s      {data['silence_gaps_over_60s']}")
    print()
    print("LATENCY to a substantive answer")
    print(f"  median {data['latency_median_s']}s   p90 {data['latency_p90_s']}s   max {data['latency_max_s']}s")
    print()
    print("TOOLS")
    for name, count in list(data["tools"].items())[:12]:
        print(f"  {name:28} {count}")
    print()
    print("USAGE BY TASK")
    for task, u in data["usage_by_task"].items():
        print(f"  {task:20} api={u['api_calls']:<6} out_tokens={u['output_tokens']:,}")
    print()
    print("USER ESCALATION CURVE")
    print("  bucket        msgs  profanity  avg_caps  avg_chars")
    for b in data["escalation"]:
        print(f"  {b['bucket']}    {b['messages']:>3}     {b['profanity']:>4}      {b['avg_caps']:.2f}      {b['avg_chars']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
