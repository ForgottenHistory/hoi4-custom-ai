"""Compare recorded wars (tools/battlerec.py, or a controller run) side by side.

    python tools/compare_runs.py RUN_FOLDER [RUN_FOLDER ...] [--cities Danzig Warsaw ...] [--json OUT]

Per run: days from the declaration to the target's capitulation, when each victory point fell (the --cities, else
the four most valuable, first), provinces taken per day, enemy divisions destroyed, and how the army fought:
share of divisions attacking, how many divisions each attack had, how far the fast divisions drove, supply,
strength and organisation, divisions standing idle behind the front, and (with a command record) what commands
the country sent. Province names come from the game's localisation, so mods' maps work too.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

import _bridge  # noqa: F401
from hoi4ai import mapdata, units
from hoi4ai.gamefiles import GameFiles

FMT = "%Y-%m-%d %H:00"


def vp_names(files):
    out = {}
    for name, path in files.listdir("localisation/english", ".yml").items():
        if "victory" not in name.lower():
            continue
        for mt in re.finditer(r'VICTORY_POINTS_(\d+):\d*\s+"([^"]*)"',
                              open(path, encoding="utf-8-sig", errors="replace").read()):
            out.setdefault(int(mt.group(1)), mt.group(2))
    return out


def read_tsv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        cols = f.readline().rstrip("\n").split("\t")
        return [dict(zip(cols, line.rstrip("\n").split("\t"))) for line in f]


class Run:
    def __init__(self, folder):
        self.folder = folder
        self.meta = json.load(open(os.path.join(folder, "meta.json"), encoding="utf-8"))
        self.tags = {int(k): v for k, v in self.meta["tags"].items()}
        self.ids = {v: k for k, v in self.tags.items()}
        self.label = self.meta["label"]
        self.status = read_tsv(os.path.join(folder, "status.tsv"))
        self.steps = [s["date"] for s in self.status]
        self.divs = collections.defaultdict(list)
        old = self.meta.get("fields", 1) < 2
        for r in read_tsv(os.path.join(folder, "divisions.tsv")):
            if old:  # recorded with the wrong fields: supply was the organisation share, share the strength
                r["org"], r["strength"], r["supply"] = r["supply"], str(int(float(r.get("share") or 1) * 100000)), "100000"
            self.divs[r["date"]].append(r)
        self.control = {}  # date -> {province: controller id} (full, rebuilt from the changes)
        cur = {}
        changes = collections.defaultdict(list)
        for r in read_tsv(os.path.join(folder, "control.tsv")):
            changes[r["date"]].append((int(r["province"]), int(r["controller"])))
        for d in self.steps:
            for p, c in changes.get(d, ()):
                cur[p] = c
            self.control[d] = dict(cur)


def bfs_depth(m, sources, passable):
    """{province: steps from SOURCES} over PASSABLE land."""
    dist = {p: 0 for p in sources}
    todo = collections.deque(sources)
    while todo:
        p = todo.popleft()
        for n in m.neighbours.get(p, {}):
            if n not in dist and n in passable:
                dist[n] = dist[p] + 1
                todo.append(n)
    return dist


def analyse(run, m, names, fast_units, cities):
    tag, target = run.meta["tag"], run.meta["target"]
    me = run.ids[tag]
    war = [s for s in run.status if s["at_war"] == "1"]
    if not war:
        return {"label": run.label, "folder": run.folder, "error": "no war in the recording"}
    t0 = datetime.datetime.strptime(war[0]["date"], FMT)
    end = next((s for s in run.status if s["capitulated"] == "1"), None)
    t1 = datetime.datetime.strptime((end or run.status[-1])["date"], FMT)
    days = lambda d: (datetime.datetime.strptime(d, FMT) - t0).total_seconds() / 86400
    start_ctrl = run.control[war[0]["date"]]
    enemies0 = {run.ids[target]}  # the target's land and divisions (its allies fight elsewhere)
    enemy_land = {p for p, c in start_ctrl.items() if c in enemies0 and m.is_land(p)}
    own0 = {p for p, c in start_ctrl.items() if c == me and m.is_land(p)}
    depth = bfs_depth(m, [p for p in own0 if any(n in enemy_land for n in m.neighbours.get(p, {}))], enemy_land)

    # victory points: every VP province that was the enemies' at the declaration, or that we took during the war
    vps = {}
    for st in m.states.values():
        vps.update(st.victory_points)
    war_steps = [s["date"] for s in run.status if datetime.datetime.strptime(s["date"], FMT) >= t0]
    fell = {}
    last = run.control[war_steps[-1]] if war_steps else {}
    for p in vps:
        if p not in enemy_land and not (last.get(p) == me and start_ctrl.get(p) != me):
            continue
        for d in war_steps:
            if run.control[d].get(p) == me:
                fell[p] = days(d)
                break
    vp_rows = sorted(({"province": p, "name": names.get(p, str(p)), "value": vps[p], "day": fell.get(p)}
                      for p in vps if p in enemy_land or p in fell), key=lambda r: -r["value"])
    want = [c.lower() for c in cities] if cities else [r["name"].lower() for r in vp_rows[:4]]

    def city_day(c):
        hit = [r for r in vp_rows if r["name"].lower().startswith(c)]
        return hit[0]["day"] if hit else None

    series = []
    agg = collections.defaultdict(list)
    for d in war_steps:
        ctrl = run.control[d]
        at = {run.ids[t] for t in next(s for s in run.status if s["date"] == d)["enemies"].split() if t in run.ids}
        hostile = {p for p, c in ctrl.items() if c in at}
        taken = sum(1 for p in enemy_land if ctrl.get(p) == me)
        ours = [x for x in run.divs[d] if x["owner"] == tag]
        theirs = [x for x in run.divs[d] if x["owner"] == target]
        front = {p for p, c in ctrl.items() if c == me and any(n in hostile for n in m.neighbours.get(p, {}))}
        # where each division steps next: the next province on its path (battle plans move divisions without a
        # destination); older recordings have only the destination of a move order
        step = lambda x: int(x["next"]) if "next" in x else (int(x["dest"]) if x["dest"] != x["province"] else 0)
        attacking = [x for x in ours if step(x) in hostile and step(x) in m.neighbours.get(int(x["province"]), {})]
        attacked = collections.Counter(step(x) for x in attacking)
        fast = [x for x in ours if x["unit"] in fast_units]
        moving = lambda x: step(x) != 0
        idle = [x for x in ours if not moving(x) and int(x["province"]) not in front]
        row = dict(day=round(days(d), 2), taken=taken, enemy_divisions=len(theirs), divisions=len(ours),
                   attacking=len(attacking) / max(1, len(ours)), attacks=len(attacked),
                   per_attack=len(attacking) / max(1, len(attacked)),
                   strength=sum(int(x["strength"]) for x in ours) / max(1, len(ours)) / 1000,
                   org=sum(int(x["org"]) for x in ours) / max(1, len(ours)) / 1000,
                   short_supply=sum(int(x["supply"]) < 95000 for x in ours) / max(1, len(ours)),
                   idle=len(idle) / max(1, len(ours)), front=len(front),
                   fast_depth=sum(depth.get(int(x["province"]), 0) for x in fast) / max(1, len(fast)),
                   fast_attacking=sum(x in attacking for x in fast) / max(1, len(fast)),
                   fast_strength=sum(int(x["strength"]) for x in fast) / max(1, len(fast)) / 1000)
        series.append(row)
        for k, v in row.items():
            agg[k].append(v)
    mean = lambda k: sum(agg[k]) / max(1, len(agg[k]))

    commands = collections.Counter()
    rec = os.path.join(run.folder, "command_record.tsv")
    if os.path.exists(rec):
        with open(rec, encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split("\t", 6)
                if len(p) > 4 and p[3] == tag and "DROPPED" not in line and "Command" in p[2]:
                    commands[p[2] + (" (ours)" if "SRC=bridge" in line else "")] += 1
    enemy_divs = [r["enemy_divisions"] for r in series]
    return {
        "label": run.label, "folder": run.folder, "war_start": war[0]["date"], "end": (end or run.status[-1])["date"],
        "capitulated": end is not None, "days": round((t1 - t0).total_seconds() / 86400, 1),
        "enemy_provinces": len(enemy_land), "cities": {c: city_day(c) for c in want}, "victory_points": vp_rows,
        "enemy_divisions_start": enemy_divs[0] if enemy_divs else 0,
        "enemy_divisions_peak": max(enemy_divs) if enemy_divs else 0,
        "means": {k: round(mean(k), 3) for k in ("attacking", "attacks", "per_attack", "strength", "org",
                                                   "short_supply", "idle", "fast_depth", "fast_attacking",
                                                   "fast_strength")},
        "commands": dict(commands.most_common(15)), "series": series,
        "paths": any("next" in x for rows in run.divs.values() for x in rows[:1]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--cities", nargs="*", default=[], help="victory points to show first (names, any prefix)")
    ap.add_argument("--json", help="write every number (and the per-step series) here")
    a = ap.parse_args()
    files = GameFiles()
    m = mapdata.load()
    names = vp_names(files)
    fast_units = {n for n, u in units.load(files).items() if u.fast}
    out = [analyse(Run(f), m, names, fast_units, a.cities) for f in a.runs]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ok = [r for r in out if "error" not in r]
    for r in out:
        if "error" in r:
            print(r["label"], r["folder"], r["error"])
    if ok:
        w = 22
        line = lambda name, vals: print(f"{name:<{w}}" + "".join(f"{v:>14}" for v in vals))
        line("", [r["label"] for r in ok])
        line("days", [f"{r['days']}{'' if r['capitulated'] else '+'}" for r in ok])
        for c in ok[0]["cities"]:
            line(f"  {c} fell (day)", [("-" if r["cities"].get(c) is None else f"{r['cities'][c]:.1f}") for r in ok])
        line("enemy divs start/peak", [f"{r['enemy_divisions_start']}/{r['enemy_divisions_peak']}" for r in ok])
        labels = {"attacking": "divs attacking", "attacks": "attacks at once", "per_attack": "divs per attack",
                  "strength": "strength %", "org": "organisation", "short_supply": "short of supply",
                  "idle": "idle behind front", "fast_depth": "fast: depth (prov)", "fast_attacking": "fast attacking",
                  "fast_strength": "fast strength %"}
        for k, name in labels.items():
            line(name, [f"{r['means'][k]:.2f}" for r in ok])
        old = [r["label"] for r in ok if not r["paths"]]
        if old:
            print("(recorded before paths: attacks and idle count only move orders, not battle plans:",
                  ", ".join(old) + ")")
        print("\nprovinces taken by day:")
        for day in range(0, int(max(r["days"] for r in ok)) + 2, 2):
            vals = []
            for r in ok:
                past = [s["taken"] for s in r["series"] if s["day"] <= day]
                vals.append(f"{past[-1] if past else 0}/{r['enemy_provinces']}")
            line(f"  day {day}", vals)
        for r in ok:
            print(f"\n{r['label']}: commands sent (not dropped):", ", ".join(f"{k} {v}" for k, v in r["commands"].items()))
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), indent=1)
        print("\nwrote", a.json)


if __name__ == "__main__":
    main()
