"""Run a country's army with our own planner (hoi4ai/front.py) instead of the game's AI.

    python tools/controller.py --tag GER --target POL [--hours 4] [--dry-run] [--no-takeover]
    python tools/controller.py --tag POL --target GER --defend

Start a new game, `observe` (or play another country), pause, then run this. It takes the country's army away
from the game AI (tools/takeover.py), then every --hours in-game hours: reads every division and who controls
each province, plans (hold the border, mass reserves at breakthrough points, attack where we're strong enough)
and sends the moves. Its divisions are protected from the AI cancelling their movement.

Logs to recordings/controller_<TAG>_<time>/: ticks.tsv (one line per planning step) and moves.tsv (every move
sent). Stops with Ctrl+C, or when the target capitulates.

--defend: TAG defends against TARGET (and its friends that border TAG) with hoi4ai/defend.py instead: one line
(hoi4ai/defence.py), a reserve behind it, a new line when it breaks. Stops when TAG capitulates. The war is
recorded from the attacker's side, like a recording of TARGET attacking TAG (tools/compare_runs.py).
"""
import argparse
import collections
import datetime
import json
import math
import os
import re
import time

import _bridge  # noqa: F401
from hoi4bridge import BridgeError, BridgeUnavailable, query, send, trigger
from hoi4ai import defence, defend, leaders as leaders_mod, mapdata, organize, plans, templates, units
from hoi4ai.battlerec import BattleRecorder
from hoi4ai.divisions import Snapshotter
from hoi4ai.front import Planner
from hoi4ai.live import Provinces
import takeover

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIL_MIN_PIXELS = 150  # before the war a division sent farther than this (map pixels, about 6 provinces) goes by rail
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def game_time(tag):
    """In-game datetime, from ' 22:00, 18 August, 1939'."""
    lines = query(f'{tag} = {{ log = "[GetDateText]" }}')
    if not lines:
        raise ValueError("no game running (main menu or loading?)")
    text = lines[0].strip()
    hm, day_month, year = [x.strip() for x in text.split(",")]
    day, month = day_month.split()
    hour = int(hm.split(":")[0])  # the game shows midnight as 24:00 of the day before
    return datetime.datetime(int(year), MONTHS[month.lower()], int(day)) + datetime.timedelta(hours=hour)


def country_ids(max_id=1000):
    tags, ids = {}, {}
    for lo in range(1, max_id, 100):
        reply = send("bridge.tag " + " ".join(str(i) for i in range(lo, lo + 100)))[0]
        for kv in reply[3:].split():
            k, v = kv.split("=")
            if v != "?":
                tags[int(k)] = v
                ids[v] = int(k)
    return tags, ids


def _tags(lines):
    """Tags from log lines of "[THIS.GetFlag]" (flag markup around the tag; GetTag gives the name)."""
    out = []
    for line in lines:
        mt = re.search(r"([A-Z][A-Z0-9]{2})\s*$", line)
        if mt:
            out.append(mt.group(1))
    return out


def friends_of(tag):
    """Countries whose land our front runs along: subjects, overlord, faction members. (Not mere military
    access: a neutral's border is not ours to hold or attack from.)"""
    lines = query(f'{tag} = {{ every_other_country = {{ limit = {{ OR = {{ is_subject_of = {tag} '
                  f'{tag} = {{ is_subject_of = PREV }} is_in_faction_with = {tag} }} }} '
                  f'log = "[THIS.GetFlag]" }} }}')
    return _tags(lines)


def _object_address(type_, oid):
    m = re.search(r"@0x([0-9a-fA-F]+)", send(f"bridge.object {type_} {oid}")[0])
    return int(m.group(1), 16) if m else 0


def _pointer_at(addr):
    r = send(f"bridge.mem {addr:x} 8")[0]
    if not r.startswith("OK"):
        return 0
    return int.from_bytes(bytes.fromhex(r[3:].strip().replace("?", "0")), "little")


def keep_in_group(gid, army_ids):
    """Put back armies that are no longer in the army group (an army's group is at +0x1b8). Only the missing ones:
    assigning an army that is already in the group makes the game re-order it. Returns the reply, or None."""
    group = _object_address(53, gid)
    missing = [a for a in army_ids if _pointer_at(_object_address(53, a) + 0x1b8) != group]
    if not group or not missing:
        return None
    return send(f"bridge.army assign {gid} " + " ".join(map(str, missing)))[0]


def keep_leaders(tag, gid, army_ids, cache, other_armies=()):
    """Every army keeps a general and the army group a field marshal. Nobody is a field marshal at the start; the game
    promotes some later (in 1939 Rundstedt, Kluge, Kesselring), which takes them out of their armies. So: a promoted
    field marshal gets the army group, an army without a general (or led by a field marshal) gets the best free one.
    ARMY_IDS in priority order (the fast army first); OTHER_ARMIES keep their generals (theirs aren't free). Only
    leaders the game lets command now are chosen (leaders.usable). Returns lines describing what changed."""
    lead = leaders_mod.load(tag, os.path.join(cache, "leaders.tsv"))
    by_addr = {_object_address(4713, l.id): l for l in lead}
    current = {a: by_addr.get(_pointer_at(_object_address(53, a) + 0x88)) for a in army_ids}
    used = {l.id for l in current.values() if l}
    used |= {l.id for l in (by_addr.get(_pointer_at(_object_address(53, a) + 0x88)) for a in other_armies) if l}
    lead = leaders_mod.usable(tag, lead)
    free = sorted((l for l in lead if not l.field_marshal and l.id not in used), key=lambda l: (-l.attack, -l.planning))
    lines = []
    for a in army_ids:
        l = current[a]
        if l and not l.field_marshal:
            continue
        if not free:
            break
        g = free.pop(0)
        r = send(f"bridge.army leader {a} {g.id}")[0]
        lines.append(f"army {a}: general {g.name} (attack {g.attack})" + ("" if r.startswith("OK") else f": {r}"))
    if gid:
        l = by_addr.get(_pointer_at(_object_address(53, gid) + 0x88))
        marshals = sorted((x for x in lead if x.field_marshal), key=lambda x: (-x.attack, -x.planning))
        if marshals and not (l and l.field_marshal):
            fm = marshals[0]
            r = send(f"bridge.army leader {gid} {fm.id}")[0]
            lines.append(f"army group {gid}: field marshal {fm.name} (attack {fm.attack})"
                         + ("" if r.startswith("OK") else f": {r}"))
    return lines


def build_structure(m, provinces, divs, tag, me, tgt, fast_units, friend_ids, cache):
    """Build the armies, generals and the army group (hoi4ai/organize.py) with bridge.army.
    Returns (summary lines, army group id or 0, the group's army ids, {division id: army id})."""
    lead = leaders_mod.usable(tag, leaders_mod.load(tag, os.path.join(cache, "leaders.tsv")))
    from hoi4ai.memory import Process
    secs = plans.sections(Process(), me, tgt, provinces.controller)
    own = {p for p in m.provinces if m.is_land(p) and provinces.controller[p] in {me, *friend_ids}}
    group, others, marshal = organize.plan(m, provinces.controller, divs, tag, me, tgt, fast_units, lead, friend_ids,
                                           along=plans.along(m, secs, own) if secs else None)
    lines, group_ids, membership = [], [], {}
    for army in group + others:
        r = send("bridge.army create " + " ".join(str(d.id) for d in army.divisions))[0]
        if not r.startswith("OK"):
            lines.append(f"{army.role}: {r}")
            continue
        aid = int(r.split()[2])
        membership.update({d.id: aid for d in army.divisions})
        if army in group:
            group_ids.append(aid)
        led = ""
        if army.leader:
            lr = send(f"bridge.army leader {aid} {army.leader.id}")[0]
            led = f", {army.leader.name} (attack {army.leader.attack})" if lr.startswith("OK") else f", leader: {lr}"
        lines.append(f"{army.role}: army {aid}, {len(army.divisions)} divisions{led}")
    if len(group_ids) > 1:
        r = send("bridge.army group " + " ".join(map(str, group_ids)))[0]
        if r.startswith("OK"):
            gid = int(r.split()[3])
            lines.append("  " + (keep_in_group(gid, group_ids) or f"all {len(group_ids)} armies in the group"))
            fm = ""
            if marshal:
                lr = send(f"bridge.army leader {gid} {marshal.id}")[0]
                fm = f", field marshal {marshal.name} (attack {marshal.attack})" if lr.startswith("OK") else f", field marshal: {lr}"
            lines.append(f"army group {gid} of {len(group_ids)} armies{fm}")
        else:
            lines.append("army group: " + r)
            gid = 0
    else:
        gid = 0
    return lines, gid, group_ids, membership


def enemies_of(tag):
    return _tags(query(f'{tag} = {{ every_enemy_country = {{ log = "[THIS.GetFlag]" }} }}'))


class Log:
    def __init__(self, folder):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self.ticks = open(os.path.join(folder, "ticks.tsv"), "a", encoding="utf-8")
        self.moves = open(os.path.join(folder, "moves.tsv"), "a", encoding="utf-8")
        self.moves.write("time\tdivision\tfrom\tto\tkind\tresult\n")
        self._cols = None

    COLS = ["date", "at_war", "enemies", "divisions", "fast", "front", "main_front", "fast_points",
            "support_points", "fast_attacks", "fast_attacks_divisions", "attacks", "attacks_divisions", "stops",
            "unposted", "short_of_supply", "max_stack", "orders", "moves", "failed", "our_power", "enemy_power",
            "target_provinces", "taken", "goal", "capitulated", "ms"]

    def tick(self, row):
        if self._cols is None:
            self._cols = self.COLS + [k for k in row if k not in self.COLS]
            self.ticks.write("\t".join(self._cols) + "\n")
        self.ticks.write("\t".join(str(row.get(c, "")) for c in self._cols) + "\n")
        self.ticks.flush()
        self.moves.flush()

    def move(self, when, d, to, kind, result):
        self.moves.write(f"{when}\t{d.id}\t{d.province}\t{to}\t{kind}\t{result}\n")


def _alive(pid):
    """Is process PID running (Windows)?"""
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def _lock():
    """Only one controller at a time: two fight over the same armies."""
    path = os.path.join(REPO, ".cache", "controller.pid")
    try:
        pid = int(open(path).read().strip())
        if pid != os.getpid() and _alive(pid):
            raise SystemExit(f"another controller is running (pid {pid}); stop it first")
    except (OSError, ValueError):
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(str(os.getpid()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="GER")
    ap.add_argument("--target", default="POL", help="country to attack (its border is held before the war)")
    ap.add_argument("--hours", type=int, default=4, help="in-game hours between planning steps")
    ap.add_argument("--dry-run", action="store_true", help="plan and log, send no moves")
    ap.add_argument("--once", action="store_true", help="one planning step, then stop")
    ap.add_argument("--organize", action="store_true",
                    help="build the army structure first: a fast army, infantry armies, an army group with a field marshal")
    ap.add_argument("--group", type=int, nargs="+", metavar=("GROUP", "ARMY"),
                    help="keep these armies in this army group (when not using --organize, e.g. after a restart)")
    ap.add_argument("--plans", action="store_true",
                    help="every army at the border gets a battle plan (front line + offensive line) with immobile "
                         "cohesion: planning builds up while the controller keeps moving the divisions")
    ap.add_argument("--execute", action="store_true",
                    help="with --plans: leave the armies' cohesion as it is and execute the plans at war; the game "
                         "then moves those armies and the controller only the rest")
    ap.add_argument("--armies", action="store_true",
                    help="let the game form armies and pick generals (their buffs); fronts and plans stay blocked")
    ap.add_argument("--no-takeover", action="store_true", help="don't block the game AI's army commands")
    ap.add_argument("--no-record", action="store_true", help="don't record the war (hoi4ai/battlerec.py)")
    ap.add_argument("--defend", action="store_true", help="TAG defends against TARGET (hoi4ai/defend.py)")
    a = ap.parse_args()
    if a.defend and (a.organize or a.plans or a.group):
        raise SystemExit("--defend runs without --organize, --plans or --group")
    if not a.dry_run:
        _lock()
    tag, target = a.tag.upper(), a.target.upper()

    m = mapdata.load(verbose=True)
    tags, ids = country_ids()
    if tag not in ids or target not in ids:
        raise SystemExit(f"unknown tag: {tag if tag not in ids else target}")
    me, tgt = ids[tag], ids[target]
    provinces = Provinces()
    snap = Snapshotter(os.path.join(REPO, ".cache", "controller_snap.tsv"))
    unit_types = units.load()
    fast_units = {n for n, u in unit_types.items() if u.fast}
    planner = Planner(m, me, fast_units, armored_units={n for n, u in unit_types.items() if u.armored})
    stats_day = None  # templates' stats (roles): at the start, then again when a new template shows up

    def load_stats(divs):
        from hoi4ai import templates
        from hoi4ai.memory import Process
        try:
            st = templates.country(tag, divs, Process())
        except Exception as e:  # the plan works without (every division counts as the main infantry)
            print("template stats:", e)
            return
        planner.set_stats(st)
        try:
            planner.widths.update(templates.terrain_widths())
        except OSError as e:
            print("terrain widths:", e)
        print("templates: " + "; ".join(f"{n} x{t['count']} soft {t['soft_attack']:.0f} -> "
                                         f"{planner.quality(type('D', (), {'template': n})()):.2f}"
                                         for n, t in sorted(st.items(), key=lambda x: -x[1]['count'])))
    defender = None
    if a.defend:
        defender = defend.Defender(m, defence.Defence(m, defence.Rules(), me, *defence.buildings(m)), me)
    widths = {"ours": defence.DIVISION_WIDTH, "foes": defence.DIVISION_WIDTH}

    def attack_tags(enemy_tags):
        """Who attacks us: the target and its friends that border us, and anyone at war with us."""
        ctrl = provinces.controller
        border = {int(ctrl[q]) for p in m.provinces if m.is_land(p) and ctrl[p] == me for q in m.land_neighbours(p)}
        near = {t for t in [target] + friends_of(target) if t in ids and (t == target or ids[t] in border)}
        return sorted(near | set(enemy_tags))

    def load_forces(divs, foe_tags):
        """--defend: both armies' stats per width (the combat model) and our templates' speeds (the reserve)."""
        from hoi4ai.memory import Process
        proc = Process()

        def army(tags_):
            rows = {}
            for tg in tags_:
                for name, t in templates.country(tg, [x for x in divs if x.owner == tg], proc).items():
                    rows[(tg, name)] = t
            n = sum(t["count"] for t in rows.values())
            w = sum(t["combat_width"] * t["count"] for t in rows.values())
            if not n or not w:
                return defence.DIVISION_WIDTH, None, {}
            per = {k: sum(t[k] * t["count"] for t in rows.values()) / w for k in ("soft_attack", "defense", "breakthrough")}
            return w / n, per, {name: t["maximum_speed"] for (_, name), t in rows.items()}
        try:
            widths["ours"], ours, speeds = army([tag])
            widths["foes"], theirs, _ = army(foe_tags)
        except Exception as e:  # without: every division 18 wide, division for division
            print("template stats:", e)
            return
        defender.d.set_forces(ours, theirs)
        defender.speeds = speeds
        print(f"forces per width: {tag} " + ", ".join(f"{k} {v:.1f}" for k, v in (ours or {}).items())
              + f"; {' '.join(foe_tags)} " + ", ".join(f"{k} {v:.1f}" for k, v in (theirs or {}).items()))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log = Log(os.path.join(REPO, "recordings", f"controller_{tag}_{stamp}"))
    rec = None
    if not a.dry_run and not a.no_record:  # the war as tools/compare_runs.py reads it, and every command
        # a defence is recorded from the attacker's side: like any recording of TARGET attacking TAG
        rec = BattleRecorder(log.folder, "ours", *((target, tag) if a.defend else (tag, target)), tags)
        print(send("bridge.cmdrec on " + os.path.join(log.folder, "command_record.tsv"))[0])
    if not a.no_takeover and not a.dry_run:
        takeover.take(tag, armies=a.armies)
        print("took over", tag, "from the game AI" + (" (armies and generals left to the game)" if a.armies else ""))
    if a.organize and not a.dry_run:
        provinces.refresh()
        friend_ids0 = [ids[t] for t in friends_of(tag) if t in ids]
        lines, group_id, group_armies, membership = build_structure(m, provinces, snap.take(), tag, me, tgt,
                                                                    fast_units, friend_ids0, os.path.join(REPO, ".cache"))
        for line in lines:
            print("  " + line)
    elif a.group and len(a.group) > 1:
        group_id, group_armies = a.group[0], a.group[1:]
        # after a restart: keep each division of these armies in the army it is in now
        membership = {d.id: d.army for d in snap.take() if d.owner == tag and d.army in group_armies}
    else:
        group_id, group_armies, membership = 0, [], {}
    army_order = list(dict.fromkeys(membership.values()))  # creation order: the fast army first
    if a.group and not a.organize:
        army_order = group_armies
    leader_day = None
    planned, executed, zone_specs = set(), False, []
    drawn = {}  # army id -> (front, start, end, target): its plan, redrawn if the game drops it
    redrawn_day = {}  # army id -> the day its front line was last redrawn (in war: at most once a day)
    state_path = os.path.join(REPO, ".cache", "plans_state.json")
    if a.plans and group_armies and not a.dry_run and os.path.exists(state_path):
        # a restart: keep the plans the game holds (redrawing them would throw away the planning built up)
        from hoi4ai.memory import Process
        st = json.load(open(state_path, encoding="utf-8"))
        proc = Process()
        # every army still holds its lines before the war; once fighting, the game uses lines up and splits them,
        # so any line left is enough (redrawing would throw away the planning)
        held = [bool(plans.lines(proc, aid)) for aid in st["planned"]]
        # (with each army's plan saved, a missing one is simply drawn again below)
        if st.get("group") == group_armies and (all(held) or (any(held) and (target in enemies_of(tag)
                                                                             or st.get("drawn")))):
            planned = set(st["planned"])
            drawn = {int(k): tuple(tuple(x) if isinstance(x, list) else x for x in v)
                     for k, v in st.get("drawn", {}).items()}
            zone_specs = [(aid, {int(k): v for k, v in b.items()}, lo, hi) for aid, b, lo, hi in st["specs"]]
            # the same border dict for armies on the same border (zones() groups by it)
            same = {}
            zone_specs = [(aid, same.setdefault(tuple(sorted(b.items())), b), lo, hi) for aid, b, lo, hi in zone_specs]
            print(f"keeping the battle plans of armies {sorted(planned)}")
    if a.plans and group_armies and not a.dry_run and not planned:
        from hoi4ai.memory import Process
        provinces.refresh()
        divs0 = snap.take()
        # every army that stands at the border (the fast one too); a restart redraws the plans
        planning = sorted({d.army for d in divs0 if d.owner == tag and d.army})
        proc = Process()
        for line in plans.clear(proc, planning):  # the game AI's lines, or ours from before a restart
            print(line)
        secs = plans.sections(proc, me, tgt, provinces.controller)
        vps = {p: v for st in m.states.values() for p, v in st.victory_points.items()
               if provinces.controller[p] == tgt}
        if secs and vps:
            armies = {aid: [d for d in divs0 if d.owner == tag and d.army == aid] for aid in planning}
            # where the game puts each percent of each border: measured with test lines on the fast army
            probe = group_armies[0]
            # (+0x50 of a line may pick the CFront's section: try a few, keep what reaches new provinces)
            borders, reached = {}, set()
            for f in sorted({x.front for x in secs}):
                for sel in (2, 1, 3, 0):
                    b = plans.calibrate(proc, probe, f, [d.id for d in armies.get(probe, [])], sel=sel)
                    if b and not set(b) <= reached:
                        borders[(f, sel)] = b
                        reached |= set(b)
            print("borders:", ", ".join(f"{f} s={sel} ({len(b)} provinces)" for (f, sel), b in borders.items()))
            # each army on the border most of its divisions are nearest to
            near = {}
            todo = collections.deque()
            for f, b in borders.items():
                for p in b:
                    near.setdefault(p, f)
                    todo.append(p)
            while todo:
                p = todo.popleft()
                for n in m.neighbours.get(p, {}):
                    if n not in near and m.is_land(n) and provinces.controller[n] != tgt:
                        near[n] = near[p]
                        todo.append(n)
            on = collections.defaultdict(dict)
            for aid, ds in armies.items():
                votes = collections.Counter(near[d.province] for d in ds if d.province in near)
                if votes:
                    on[votes.most_common(1)[0][0]][aid] = ds
            for f, group in on.items():
                laid = plans.make(m, group, borders[f], f, vps, provinces.controller, tgt, fast=group_armies[0],
                                  always=group_armies)
                zone_specs += [(aid, borders[f], start / plans.FULL, end / plans.FULL)
                               for aid, _, start, end, _, _ in laid if aid != group_armies[0]]
                for aid, ok, line in plans.draw(proc, laid):
                    print(line)
                    if ok:
                        planned.add(aid)
                drawn.update({aid: (front, start, end, t) for aid, front, start, end, t, _ in laid if aid in planned})
            if not a.execute:  # the game keeps the plans (planning builds) but leaves the moving to us
                for line in plans.cohesion(sorted(planned)):
                    print(line)
            json.dump({"group": group_armies, "planned": sorted(planned), "drawn": {str(k): v for k, v in drawn.items()},
                       "specs": [(aid, {str(k): v for k, v in b.items()}, lo, hi) for aid, b, lo, hi in zone_specs]},
                      open(state_path, "w", encoding="utf-8"))
        else:
            print("battle plans: no border with", target, "found" if not secs else "", "or no victory points")
    ticks = 0
    can_stop = send("bridge.stop")[0].startswith("FAIL usage: bridge.stop")
    can_rail = send("bridge.rail")[0].startswith("FAIL usage: bridge.rail")
    print("stopping attacks with", "bridge.stop" if can_stop else "a move back to the division's own province")
    protected = set()
    railing = {}  # division id -> [rail target, last province seen, steps without moving]: on a train, left alone
    war_start = None
    last = None
    print(f"running {tag} against {target}, a plan every {a.hours} in-game hours; logs in {log.folder}")
    try:
        while True:
            try:
                now = game_time(tag)
            except (BridgeError, BridgeUnavailable, ValueError) as e:
                print("date:", e)
                time.sleep(2)
                continue
            if last and now - last < datetime.timedelta(hours=a.hours):
                time.sleep(0.25)
                continue
            last = now
            t0 = time.time()
            ticks += 1
            if group_id:
                r = keep_in_group(group_id, group_armies)
                if r:
                    print("army group, put back:", r)
            try:
                provinces.refresh()
                divs = snap.take()
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("snapshot:", e)
                last = None
                time.sleep(1)
                continue
            if a.defend:
                if now.date() != stats_day and (stats_day is None or any(
                        d.owner == tag and d.template not in defender.speeds for d in divs)):
                    stats_day = now.date()
                    load_forces(divs, attack_tags(enemies_of(tag)))
            elif now.date() != stats_day and (stats_day is None or any(
                    d.owner == tag and d.template not in planner.stats for d in divs)):
                stats_day = now.date()
                load_stats(divs)
            if army_order and not a.dry_run and now.date() != leader_day:  # once a day: generals and field marshal
                leader_day = now.date()
                # every army of ours: the group's first (the fast one first), then the others (garrisons, exclaves)
                others = sorted({d.army for d in divs if d.owner == tag and d.army} - set(army_order) - {group_id})
                for line in keep_leaders(tag, group_id, list(army_order) + others, os.path.join(REPO, ".cache")):
                    print(line)
            enemy_tags = enemies_of(tag)
            enemy_ids = [ids[t] for t in enemy_tags if t in ids]
            friend_ids = [ids[t] for t in friends_of(tag) if t in ids]
            at_war = target in enemy_tags
            if at_war and war_start is None:
                war_start = now
            if drawn and not a.dry_run:
                # the game sometimes drops an army's front line or offensive line on its own (no command does it;
                # two armies lost theirs when the war was declared): draw it again. In war only a lost front line
                # (an offensive line can be finished), once a day per army
                from hoi4ai.memory import Process
                proc = Process()
                lost, fixed = [], []
                for aid, p in sorted(drawn.items()):
                    ds = [d.id for d in divs if d.owner == tag and d.army == aid]
                    have = plans.arrows(proc, aid)
                    if not have:
                        if at_war and redrawn_day.get(aid) == now.date():
                            continue
                        redrawn_day[aid] = now.date()
                        lost.append((aid, *p, ds))
                    elif not at_war and not any(have.values()) and ds:
                        r = send(f"bridge.plan arrow {aid} #{min(have)} {p[3]} " + " ".join(map(str, ds)))[0]
                        print(f"redrawn: army {aid}: offensive to {p[3]} on line {min(have)}: {r}")
                        fixed.append(aid)
                for aid, ok, line in plans.draw(proc, lost):
                    print("redrawn:", line)
                if (lost or fixed) and not a.execute:
                    for line in plans.cohesion([x[0] for x in lost] + fixed):
                        print(line)
            if planned and a.execute and at_war and not executed:
                for line in plans.execute(sorted(planned)):
                    print(line)
                executed = True
            # with --execute, divisions of armies on a battle plan are the game's to move
            # each army with a front line holds its own stretch (where its plan is)
            zone = plans.zones(m, zone_specs, provinces.controller, {me, *friend_ids}) if zone_specs else None
            if a.defend:
                foe_tags = attack_tags(enemy_tags)
                at_war = bool(set(foe_tags) & set(enemy_tags))
                if at_war and war_start is None:
                    war_start = now
                orders, rep = defender.plan(provinces.controller, provinces.owner, divs, tag,
                                            {ids[t] for t in foe_tags}, at_war,
                                            [d for d in divs if d.owner in foe_tags], widths["ours"], widths["foes"],
                                            hour=(now - datetime.datetime(1900, 1, 1)).total_seconds() / 3600)
                stops = defender.stops
            else:
                orders, rep = planner.plan(provinces.controller,
                                           [d for d in divs if not (a.execute and d.owner == tag and d.army in planned)],
                                           tag, enemy_ids, tgt, at_war, friend_ids, zones=zone)
                stops = planner.stops
            mine = {d.id: d for d in divs if d.owner == tag}
            if membership and not a.dry_run:  # divisions that drifted out of their army go back
                strays = collections.defaultdict(list)
                for did, aid in membership.items():
                    d = mine.get(did)
                    if d is not None and d.army != aid:
                        strays[aid].append(did)
                for aid, dids in strays.items():
                    r = send(f"bridge.army add {aid} " + " ".join(map(str, dids)))[0]
                    print(f"army {aid}: put back {len(dids)} divisions: {r}")
            new = [i for i in mine if i not in protected]
            if new and not a.dry_run:
                send("bridge.protect add " + " ".join(map(str, new)))
                protected.update(new)
            sent = failed = 0
            cmds, batch = [], []
            for did, r in list(railing.items()):  # trains: arrived, stuck (no progress for 2 steps) or the war began
                d = mine.get(did)
                if d is None or d.province == r[0] or at_war:
                    del railing[did]
                    continue
                r[2] = r[2] + 1 if d.province == r[1] else 0
                r[1] = d.province
                if r[2] >= 2:
                    del railing[did]
            # at war the game paths the shortest way, through enemy-held land: a division going anywhere but next
            # door is sent a few provinces along our own route instead (repositioning: through our land; a drive
            # into the enemy's rear: through our land and empty enemy land), the next stretch every step
            ctrl, hostile_ids = provinces.controller, set(enemy_ids)
            if at_war:
                foe_at = {x.province for x in divs if x.owner in set(enemy_tags)}
                ours_land = {p for p in m.provinces if m.is_land(p) and ctrl[p] in {me, *friend_ids}}
                open_land = ours_land | {p for p in m.provinces if m.is_land(p) and ctrl[p] in hostile_ids
                                         and p not in foe_at}
            for did, prov in orders.items():
                d = mine.get(did)
                if d is None or (d.province == prov and not d.moving):
                    continue
                if did in railing and railing[did][0] == prov:
                    continue  # on its way by train: a new order would take it off the train
                kind = "attack" if ctrl[prov] in hostile_ids else "post"
                if at_war and d.province != prov and prov not in planner.nb(d.province):
                    w = planner.waypoint(d.province, prov, ours_land if kind == "post" else open_land)
                    if w is not None:
                        prov = w
                if d.dest == prov:
                    continue
                pa, pb = m.provinces.get(d.province), m.provinces[prov]
                if (can_rail and not at_war and kind == "post" and pa is not None
                        and math.hypot(pa.x - pb.x, pa.y - pb.y) > RAIL_MIN_PIXELS):
                    kind = "rail"  # a long way in peacetime: by train (strategic redeployment)
                cmds.append(f"bridge.rail {did} {prov}" if kind == "rail" else f"bridge.move {did} {prov}")
                batch.append((d, prov, kind))
            for d in stops:  # attacks to call off
                cmds.append(f"bridge.stop {d.id}" if can_stop else f"bridge.move {d.id} {d.province}")
                batch.append((d, d.province, "stop"))
            if cmds and not a.dry_run:
                for i in range(0, len(cmds), 40):
                    for (d, prov, kind), r in zip(batch[i:i + 40], send(*cmds[i:i + 40])):
                        if kind == "rail" and not r.startswith("OK"):  # no rail route: march
                            kind, r = "post", send(f"bridge.move {d.id} {prov}")[0]
                        ok = r.startswith("OK")
                        if ok and kind == "rail":
                            railing[d.id] = [prov, d.province, 0]
                        sent += ok
                        failed += not ok
                        log.move(now, d, prov, kind, "ok" if ok else r)
            ctrl = provinces.controller
            if a.defend:  # our land, and how much of it the enemy holds
                target_land = [p for p in m.provinces if m.is_land(p) and provinces.owner[p] == me]
                taken = sum(1 for p in target_land if ctrl[p] != me)
                capitulated = at_war and trigger(f"{tag} = {{ has_capitulated = yes }}")
            else:
                target_land = [p for p in m.provinces if m.is_land(p) and provinces.owner[p] == tgt]
                taken = sum(1 for p in target_land if ctrl[p] == me)
                capitulated = at_war and trigger(f"{target} = {{ has_capitulated = yes }}")
            row = dict(date=now.strftime("%Y-%m-%d %H:00"), at_war=int(at_war), enemies=" ".join(enemy_tags),
                       **rep, orders=len(orders), moves=sent, failed=failed,
                       target_provinces=len(target_land), taken=taken, capitulated=int(capitulated),
                       ms=int((time.time() - t0) * 1000))
            log.tick(row)
            if rec:
                rec.step(now, divs, ctrl, enemies_of(target) if a.defend else enemy_tags, at_war, capitulated)
                send(f"bridge.cmdrec mark date={row['date']}")
            if a.defend:
                print(f"{row['date']} war={row['at_war']} divs={rep['divisions']} line={rep['front']} provinces "
                      f"({rep.get('line_vp', 0):.0%} VP, need {rep.get('line_need', 0)}) held={rep.get('held', 0)} "
                      f"reserve={rep.get('reserve', 0)} lost_behind={rep.get('lost_behind', 0)} "
                      f"counters={rep.get('counters', 0)} blocks={rep.get('blocks', 0)} lines={rep.get('lines', 0)} "
                      f"moves={sent} fail={failed} lost={taken}/{len(target_land)} ({row['ms']} ms)")
            else:
              print(f"{row['date']} war={row['at_war']} divs={rep['divisions']} front={rep['front']} "
                  f"fast={rep['fast']} attacks={rep.get('fast_attacks', 0)}+{rep.get('attacks', 0)} drives={rep.get('drives', 0)}+{rep.get('infantry_drives', 0)} resting={rep.get('resting', 0)} moves={sent} fail={failed} "
                  f"taken={taken}/{len(target_land)} short_of_supply={rep['short_of_supply']} ({row['ms']} ms)")
            if a.once:
                for did, prov in list(orders.items())[:15]:
                    d = mine.get(did)
                    print("  ", d, "->", prov)
                break
            if capitulated:
                days = (now - war_start).total_seconds() / 86400 if war_start else 0
                print(f"{tag if a.defend else target} capitulated on {row['date']}, {days:.1f} days after the war began")
                break
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if rec:
            print(send("bridge.cmdrec off")[0])


if __name__ == "__main__":
    main()
