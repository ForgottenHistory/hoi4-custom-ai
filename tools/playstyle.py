"""How the player played a recorded campaign: one JSON file of timelines and monthly counts, for a report page.

    python playstyle.py RECORDING_DIR [--out FILE]

Reads examples.jsonl (run examples.py first), country.tsv, unit_snapshots.tsv and the logs of the folder, and
writes RECORDING_DIR/playstyle.json:
  meta        player, span, mods, examples
  wars        each war of the player: enemy, start (the game's own date of the war), end
  economy     per country.tsv row: factories (civilian, military, naval), manpower, political power, stability,
              war support, states, divisions
  production  per month: military factories on each equipment kind (replayed from production_lines.tsv)
  army        per day: the player's divisions, mean strength, how many moved since the day before;
              per month: the player's army commands by kind (moves, rail moves, halts, plans,
              army organisation, air)
  choices     focus picks (with the focus finishing), laws and advisors, decisions taken, research picks, event
              choices where there was a choice, templates, construction the player queued; each with its date
              (the command's where examples.py found it, see "logged")
  wars_react  per war: the player's commands by kind in the 14 days before and after it started, and the
              non-army choices made in the 14 days after
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from examples import player_of, rows, when  # noqa: E402

KINDS = {
    "move": ("CMoveCommand", "CMassMoveCommand"),
    "rail": ("CStrategicRedeploymentCommand",),
    "halt": ("CCancelMovementCommand",),
    "plan": ("COrderNewFrontCommand", "COrderSetPathCommand", "COrderDeleteCommand", "COrderDeleteAllCommand",
             "COrderExecuteCommand", "COrderEditRootCommand", "COrderNewRootCommand", "COrderReplaceRootCommands",
             "CSetOrderGroupExecutionTypeCommand", "CEditAreaDefenseStateCommand", "CCreateAreaDefenseCommand"),
    "organise": ("COrderAssignCommand", "COrderUnassignCommand", "COrderGroupCommand", "CAssignToArmyGroupCommand",
                 "CSetArmyLeaderCommand", "CArmyGroupCommand", "COrderSetTrainingCommand"),
    "air": ("CStratAirTransferCommand", "CStratAirSetMissionCommand", "CStratAirEnableMissionCommand",
            "CStratAirCancelTransferCommand", "CStratAirConsolidateCommand"),
}
KIND_OF = {c: k for k, cs in KINDS.items() for c in cs}
REACT = datetime.timedelta(days=14)


def month(d):
    return f"{d.year}-{d.month:02d}"


def equipment_kind(t):
    """infantry_equipment_1 -> infantry; light_tank_chassis_2 -> light_tank; convoy_1 -> convoy."""
    t = re.sub(r"_\d+$", "", t or "?")
    return re.sub(r"_(equipment|chassis|airframe|hull)$", "", t) or "?"


def load_examples(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def wars(folder, tag):
    out, open_ = [], {}
    for r in rows(os.path.join(folder, "diplomacy.tsv")):
        if r.get("country") != tag or r.get("kind") != "pact" or r.get("key") != "war_relation":
            continue
        if r["change"] == "new":
            m = re.search(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", r.get("value", ""))
            start = datetime.datetime(*map(int, m.groups()[:3]), int(m.group(4)) % 24) if m else when(r["date"])
            open_[r["partner"]] = {"enemy": r["partner"], "start": start, "end": None,
                                   "attacker": r.get("value", "").startswith(tag + ">")}
        elif r["change"] == "removed" and r["partner"] in open_:
            w = open_.pop(r["partner"])
            w["end"] = when(r["date"])
            out.append(w)
    out += open_.values()
    return sorted(out, key=lambda w: w["start"])


def economy(folder):
    out = []
    num = lambda v: float(v) if re.match(r"^-?[\d.]+$", v or "") else None  # noqa: E731
    for r in rows(os.path.join(folder, "country.tsv")):
        out.append({"date": r["date"][:10], "civ": num(r["num_of_civilian_factories"]),
                    "mil": num(r["num_of_military_factories"]), "naval": num(r["num_of_naval_factories"]),
                    "manpower": num(r["manpower"]), "pp": num(r["political_power"]),
                    "stability": num(r["stability"]), "war_support": num(r["war_support"]),
                    "states": num(r["num_of_controlled_states"]), "divisions": num(r["num_divisions"])})
    return out


def production(folder, tag, end):
    types = {}
    for name in ("equipment.tsv", "equipment_designs.tsv"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            for r in rows(path):
                types[r["id"]] = r.get("type", "")
    lines, out, cur = {}, {}, None
    for r in rows(os.path.join(folder, "production_lines.tsv")):
        if r.get("owner") != tag:
            continue
        d = when(r["date"])
        if cur and month(d) != cur:
            out[cur] = _sum(lines)
        cur = month(d)
        if r["change"] == "removed":
            lines.pop(r["line"], None)
        else:
            lines[r["line"]] = (equipment_kind(types.get(r.get("design_id"), r.get("design"))),
                                float(r.get("factories") or 0))
    if cur:
        out[cur] = _sum(lines)
    # carry the last state forward month by month to the end of the record
    filled, last = {}, {}
    if out:
        y, m = map(int, min(out).split("-"))
        while (y, m) <= (end.year, end.month):
            k = f"{y}-{m:02d}"
            last = out.get(k, last)
            filled[k] = last
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return filled


def _sum(lines):
    c = Counter()
    for kind, n in lines.values():
        c[kind] += n
    return dict(c)


def army_days(path, tag):
    """[{date, divisions, strength (mean, % of full), moved}] from unit_snapshots.tsv (the player's divisions only)."""
    out, cols, handle, day, today, prev = [], None, "51", None, {}, {}

    def flush():
        if day and today:
            moved = sum(1 for k, (p, _, _) in today.items() if k in prev and prev[k][0] != p)
            out.append({"date": day, "divisions": len(today),
                        "strength": round(sum(s for _, s, _ in today.values()) / len(today), 1),
                        "moved": moved})

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#snap":
                if p[1] != day:
                    flush()
                    if today:
                        prev = today
                    day, today = p[1], {}
                handle = p[2]
                cols = [c for c in p[3:] if not c.startswith("class=")]
                continue
            if cols is None or len(p) < len(cols) + 1 or p[1] != tag:
                continue
            r = dict(zip(cols, p[1:]))
            try:
                today[(handle, p[0])] = (r["i1e0.a4"], int(r["i30"]) / 1000, 0)
            except (KeyError, ValueError):
                pass
    flush()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    tag = player_of(os.path.join(a.dir, "command_record.tsv"))
    ex = load_examples(os.path.join(a.dir, "examples.jsonl"))
    dates = [when(e["date"]) for e in ex]
    start, end = min(dates), max(dates)

    picks, finished, laws, decisions, research, events, templates, builds = [], {}, [], [], [], [], [], []
    monthly = defaultdict(Counter)
    cmds = []
    for e, d in zip(ex, dates):
        a_, src = e["action"], e["source"]
        item = {"date": e["date"]}
        if e.get("logged"):
            item["logged"] = e["logged"]
        if src == "command":
            kind = KIND_OF.get(a_["class"], "other")
            monthly[month(d)][kind] += a_["count"]
            cmds.append((d, kind, a_["count"]))
            continue
        if src == "plans":
            continue
        if src == "politics" and a_.get("kind") == "focus":
            if a_.get("slot") == "current" and e["change"] in ("new", "changed"):
                picks.append(dict(item, focus=a_.get("value")))
            elif a_.get("slot") == "completed" and e["change"] == "new":
                finished[a_.get("value")] = e["date"]
        elif src == "politics" and a_.get("kind") in ("law", "advisor") and e["change"] in ("new", "changed"):
            laws.append(dict(item, kind=a_["kind"], slot=a_.get("slot"), value=a_.get("value"), was=e.get("was")))
        elif src == "decisions" and not e["auto"]:
            decisions.append(dict(item, decision=a_.get("decision"), target=a_.get("target")))
        elif src == "research" and not e["auto"] and a_.get("tech"):
            research.append(dict(item, tech=a_["tech"], slot=a_.get("slot")))
        elif src == "events" and not e["auto"]:
            events.append(dict(item, event=a_.get("event"), option=a_.get("option_name"), options=a_.get("options")))
        elif src == "division_templates":
            templates.append(dict(item, change=e["change"], name=a_.get("name"), battalions=a_.get("battalions"),
                                  support=a_.get("support")))
        elif src == "construction" and e["change"] == "new" and not e["auto"]:
            builds.append(dict(item, building=a_.get("building"), state=a_.get("state"), amount=a_.get("amount")))
    for p in picks:
        p["finished"] = finished.get(p["focus"])

    ws = wars(a.dir, tag)
    react = []
    for w in ws:
        s = w["start"]
        before = Counter()
        after = Counter()
        for d, kind, n in cmds:
            if s - REACT <= d < s:
                before[kind] += n
            elif s <= d < s + REACT:
                after[kind] += n
        other = [{"date": e["date"], "source": e["source"], "what": next((e["action"][k] for k in ("decision", "tech", "building", "name", "design", "value")
                                if e["action"].get(k)), None)}
                 for e, d in zip(ex, dates) if s <= d < s + REACT and not e["auto"]
                 and e["source"] in ("politics", "research", "decisions", "construction", "production_lines",
                                     "division_templates", "deployment", "logistics")]
        react.append({"enemy": w["enemy"], "start": w["start"], "before": dict(before), "after": dict(after),
                      "other": other})

    mods = []
    try:
        mods = json.load(open(os.path.join(a.dir, "dlc_load.json"), encoding="utf-8")).get("enabled_mods", [])
    except (OSError, ValueError):
        pass
    out = {
        "meta": {"player": tag, "start": start, "end": end, "mods": len(mods), "examples": len(ex),
                 "player_examples": sum(1 for e in ex if not e["auto"]), "kinds": list(KINDS) + ["other"]},
        "wars": ws, "economy": economy(a.dir), "production": production(a.dir, tag, end),
        "army": {"days": army_days(os.path.join(a.dir, "unit_snapshots.tsv"), tag),
                 "monthly": {m: dict(c) for m, c in sorted(monthly.items())}},
        "choices": {"focus": picks, "laws": laws, "decisions": decisions, "research": research, "events": events,
                    "templates": templates, "construction": builds},
        "wars_react": react,
    }
    dest = a.out or os.path.join(a.dir, "playstyle.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, default=lambda o: o.isoformat(" ", "minutes") if hasattr(o, "isoformat") else str(o))
    print(f"{tag}: {len(picks)} focus picks, {len(research)} research picks, {len(decisions)} decisions, "
          f"{len(events)} event choices, {len(ws)} wars -> {dest}")


if __name__ == "__main__":
    main()
