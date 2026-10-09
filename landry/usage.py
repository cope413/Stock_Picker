"""``landry usage``: where a Claude Code session's tokens went (read-only; reads the session transcript).

Built 2026-10-09 from the analysis behind the CLAUDE CODE Optimization review. Estimates: 3.6 characters per token,
~1.8K tokens per image, cost weights cache-read 0.1 / cache-write 1.25 / output 5 (API list proxies; a plan meter
may weigh differently). Thinking is not stored in the transcript, so it is output tokens minus what is visible; the
growth test (context growth between calls ~ previous call's TOTAL output) shows it stays in context.
Use --last N to compare a recent stretch (e.g. after changing autoCompactWindow / effort) with the whole session."""
import collections
import glob
import json
import os
import statistics
from typing import Optional

CH, IMG = 3.6, 1800


def default_session(cwd: Optional[str] = None) -> Optional[str]:
    cwd = os.path.abspath(cwd or os.getcwd())
    d = os.path.expanduser("~/.claude/projects/" + cwd.replace("/", "-").replace("_", "-"))
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime)
    return files[-1] if files else None


def parse(path: str):
    msgs, events = {}, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            t, m = o.get("type"), o.get("message") or {}
            if t == "system" and o.get("subtype") == "compact_boundary":
                events.append(("B",))
            elif t == "assistant":
                mid = m.get("id") or o.get("uuid")
                if mid not in msgs:
                    msgs[mid] = {"u": {}, "vis": 0}
                    events.append(("A", mid))
                u = m.get("usage") or {}
                if u and u.get("output_tokens", 0) >= msgs[mid]["u"].get("output_tokens", -1):
                    msgs[mid]["u"] = u
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text":
                        msgs[mid]["vis"] += len(b.get("text", ""))
                    elif isinstance(b, dict) and b.get("type") == "tool_use":
                        msgs[mid]["vis"] += len(json.dumps(b.get("input", {})))
            elif t == "user":
                c, ch, im = m.get("content"), 0, 0
                if isinstance(c, str):
                    ch = len(c)
                for b in c if isinstance(c, list) else []:
                    if not isinstance(b, dict):
                        continue
                    inner = b.get("content") if b.get("type") == "tool_result" else [b]
                    for x in [inner] if isinstance(inner, str) else (inner or []):
                        if isinstance(x, str):
                            ch += len(x)
                        elif isinstance(x, dict) and x.get("type") == "text":
                            ch += len(x.get("text", ""))
                        elif isinstance(x, dict) and x.get("type") == "image":
                            im += 1
                events.append(("R", ch, im))
    calls, seg, rc, ri = [], 0, 0, 0
    for ev in events:
        if ev[0] == "B":
            seg, rc, ri = seg + 1, 0, 0
        elif ev[0] == "R":
            rc, ri = rc + ev[1], ri + ev[2]
        else:
            d = msgs[ev[1]]
            u = d["u"]
            calls.append(dict(i=u.get("input_tokens", 0), cr=u.get("cache_read_input_tokens", 0),
                              cw=u.get("cache_creation_input_tokens", 0), out=u.get("output_tokens", 0),
                              vis=d["vis"] / CH, res=rc / CH, img=ri * IMG, seg=seg))
            rc = ri = 0
    for c in calls:
        c["ctx"] = c["i"] + c["cr"] + c["cw"]
    return calls


def report(calls, last: int = 0) -> str:
    if last:
        calls = calls[-last:]
    calls = [c for c in calls if c["ctx"]]
    if not calls:
        return "no usage data in that transcript"
    n = len(calls)
    ctx = [c["ctx"] for c in calls]
    T = {k: sum(c[k] for c in calls) for k in ("i", "cr", "cw", "out")}
    w = T["i"] + .1 * T["cr"] + 1.25 * T["cw"] + 5 * T["out"]
    hidden = sum(max(c["out"] - c["vis"], 0) for c in calls)
    growth = sum(max(b["ctx"] - a["ctx"], 0) for a, b in zip(calls, calls[1:]) if a["seg"] == b["seg"])
    q = statistics.quantiles(ctx, n=10)[8] if n >= 10 else max(ctx)
    L = [f"calls {n}  compactions {calls[-1]['seg'] - calls[0]['seg']}",
         f"context per call: median {int(statistics.median(ctx)):,}  p90 {int(q):,}  max {max(ctx):,}",
         f"tokens (M): cache-read {T['cr'] / 1e6:,.0f}  cache-write {T['cw'] / 1e6:,.1f}  output {T['out'] / 1e6:,.2f}",
         f"weighted shares: re-reads {.1 * T['cr'] / w:.0%}  writes {1.25 * T['cw'] / w:.0%}  output {5 * T['out'] / w:.0%}",
         f"per call: hidden thinking ~{hidden / n:,.0f} tok ({hidden / max(T['out'], 1):.0%} of output)  context growth ~{growth / n:,.0f} tok"]
    cats, by = collections.Counter(), collections.defaultdict(list)
    for k, c in enumerate(calls):
        by[c["seg"]].append(k)
    for idx in by.values():
        m = len(idx)
        cats["floor (system prompt, CLAUDE.md, tools, summary)"] += calls[idx[0]]["ctx"] * m
        for k, i in enumerate(idx):
            c, reads = calls[i], m - k - 1
            cats["thinking"] += max(c["out"] - c["vis"], 0) * reads
            cats["my text + tool inputs"] += min(c["vis"], c["out"]) * reads
            if k:
                cats["tool results"] += c["res"] * (m - k)
                cats["images"] += c["img"] * (m - k)
    tot = sum(ctx)
    cats["other (user msgs, reminders, re-attached files)"] = max(tot - sum(cats.values()), 0)
    L.append("what the re-reads consist of:" + ("  (--last: the window's first call is not a segment start, so 'floor' is overstated)" if last else ""))
    for k, v in cats.most_common():
        L.append(f"  {v / tot:5.1%}  {k}")
    return "\n".join(L)
