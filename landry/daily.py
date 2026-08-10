"""`landry daily` — the daily operations run.

Assembles every open decision into an action-items list with rule
citations and deadlines (the app-side version of the workbook's Action
Items tab), from: the workbook's scored names + positions, the latest
landry_snapshot.json, and the score store's pending drafts.

Review cadence implemented (Part 9): Rule 43 quarterly Watch List review
and 90-day re-scores; Rule 44 annual full re-score. Rules 45-47 are
event-driven (exits, >20% losses, 2-year weight review) and surface here
only as reminders when their inputs are present.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

ACTIONS_FILE = "landry_actions.json"

ANNUAL_DAYS = 365          # Rule 44
QUARTERLY_DAYS = 90        # Rule 43
SNAPSHOT_STALE_DAYS = 7


@dataclass
class Action:
    priority: int            # 1 = act now, 2 = due soon, 3 = housekeeping
    ticker: str
    action: str
    rule: str
    deadline: Optional[str] = None


def _days_since(d, today: _dt.date) -> Optional[int]:
    if d is None:
        return None
    if hasattr(d, "date"):
        d = d.date()
    return (today - d).days


def build_action_items(scoring_rows, positions, snapshot: Optional[dict],
                       pending: Dict[str, dict],
                       today: Optional[_dt.date] = None) -> List[Action]:
    """Pure assembly — all inputs are already-loaded objects."""
    today = today or _dt.date.today()
    held = {p.ticker for p in positions if p.asset_class == "Equity"}
    items: List[Action] = []

    # Rule 46: any loss greater than 20% from cost -> post-mortem
    for p in positions:
        upct = getattr(p, "unrealized_pct", None)
        if p.asset_class == "Equity" and upct is not None and upct < -0.20:
            items.append(Action(1, p.ticker,
                         f"Loss of {upct:.0%} from cost — run a post-mortem "
                         "and identify missed warning signals", "Rule 46"))

    for row in scoring_rows:
        t = row.ticker
        days = _days_since(row.date_scored, today)
        comp = row.composite

        if t in held:
            if comp is not None and 50 <= comp < 65:
                items.append(Action(1, t, f"Probationary Hold (score {comp:.0f})"
                             ": no additions; 90-day re-score required",
                             "Rule 32",
                             str(today + _dt.timedelta(days=QUARTERLY_DAYS))))
            elif comp is not None and 35 <= comp < 50:
                items.append(Action(1, t, f"Exit Review (score {comp:.0f}): "
                             "sell within 30 days unless a dated remediation "
                             "plan is approved", "Rule 31",
                             str(today + _dt.timedelta(days=30))))
            elif comp is not None and comp < 35:
                items.append(Action(1, t, f"Mandatory Sell (score {comp:.0f})",
                             "Rule 33", str(today + _dt.timedelta(days=30))))
            elif comp is None and row.rule_flags[2] == "AVOID":
                items.append(Action(1, t, "Held name failed the Tier 1 gate "
                             "at last scoring — Exit Review", "Rules 1/3 + 31",
                             str(today + _dt.timedelta(days=30))))
            if days is not None and days > ANNUAL_DAYS:
                items.append(Action(2, t, f"Annual re-score overdue "
                             f"({days} days since last score)", "Rule 44"))
        else:
            # not held: Watch List names get the quarterly review clock
            if comp is not None and 50 <= comp < 65 and days is not None \
                    and days > QUARTERLY_DAYS:
                items.append(Action(2, t, f"Watch List quarterly review due "
                             f"({days} days since last score)", "Rule 43"))

        if days is not None and QUARTERLY_DAYS < days <= ANNUAL_DAYS \
                and t in held and comp is not None and comp >= 65:
            items.append(Action(3, t, f"Quarterly check: {days} days since "
                         "last score — re-score if material news", "Rule 43"))

    if snapshot:
        asof = snapshot.get("asof", "")
        try:
            age = (_dt.datetime.now(_dt.timezone.utc)
                   - _dt.datetime.fromisoformat(asof)).days
            if age > SNAPSHOT_STALE_DAYS:
                items.append(Action(3, "-", f"Data snapshot is {age} days old "
                             "— run `landry refresh`", "Part 12"))
        except ValueError:
            pass
        corr = snapshot.get("correlation") or {}
        for cluster in corr.get("clusters", []):
            items.append(Action(2, "/".join(cluster),
                         "Correlated cluster (3+ pairwise > 0.70): document "
                         "the shared economic risk driver; new adds staged "
                         "at 50%", "Rule 36"))
        for ce in corr.get("cluster_exposure", []):
            if ce.get("over_cap"):
                items.append(Action(1, "/".join(ce["cluster"]),
                             ce["note"], "Rule 36"))
        macro = snapshot.get("macro") or {}
        for eff in macro.get("active_effects", []):
            items.append(Action(2, "-", f"Macro overlay active: "
                         f"{eff['condition']}", "Part 7"))

    for t, drafts in (pending or {}).items():
        items.append(Action(3, t, f"{len(drafts)} draft score(s) awaiting "
                     "approval — `landry pending " + t + "`", "Part 12"))

    return sorted(items, key=lambda a: (a.priority, a.ticker))


def run_daily(workbook_path: str, repo_dir: str, store=None,
              today: Optional[_dt.date] = None) -> List[Action]:
    """Load everything from disk and assemble the day's action items."""
    from landry.xlsx_io import read_positions, read_scoring_tab

    rows = read_scoring_tab(workbook_path)
    positions = read_positions(workbook_path)
    snap_path = os.path.join(repo_dir, "landry_snapshot.json")
    snapshot = None
    if os.path.exists(snap_path):
        with open(snap_path) as f:
            snapshot = json.load(f)
    pending = store.pending() if store is not None else {}
    return build_action_items(rows, positions, snapshot, pending, today)


def write_actions(actions: List[Action], repo_dir: str,
                  today: Optional[_dt.date] = None) -> str:
    path = os.path.join(repo_dir, ACTIONS_FILE)
    with open(path, "w") as f:
        json.dump({"date": str(today or _dt.date.today()),
                   "actions": [asdict(a) for a in actions]}, f, indent=1)
    return path


def print_actions(actions: List[Action]) -> None:
    if not actions:
        print("no open action items")
        return
    labels = {1: "ACT NOW", 2: "DUE SOON", 3: "HOUSEKEEPING"}
    current = None
    for a in actions:
        if a.priority != current:
            current = a.priority
            print(f"\n=== {labels[current]} ===")
        dl = f"  [by {a.deadline}]" if a.deadline else ""
        print(f"  {a.ticker:12s} {a.action}  ({a.rule}){dl}")
