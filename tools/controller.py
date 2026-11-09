"""Run a country's army with our own planner (hoi4ai/front.py) instead of the game's AI.

    python tools/controller.py --tag GER --target POL [--hours 4] [--dry-run] [--no-takeover]

Start a new game, `observe` (or play another country), pause, then run this. It takes the country's army away
from the game AI (tools/takeover.py), then every --hours in-game hours: reads every division and who controls
each province, plans (hold the border, mass reserves at breakthrough points, attack where we're strong enough)
and sends the moves. Its divisions are protected from the AI cancelling their movement.

Logs to recordings/controller_<TAG>_<time>/: ticks.tsv (one line per planning step) and moves.tsv (every move
sent). Stops with Ctrl+C, or when the target capitulates.
"""
import argparse
import collections
import datetime
import os
import re
import time

import _bridge  # noqa: F401
from hoi4bridge import BridgeError, BridgeUnavailable, query, send, trigger
from hoi4ai import leaders as leaders_mod, mapdata, organize, plans, units
from hoi4ai.divisions import Snapshotter
from hoi4ai.front import Planner
from hoi4ai.live import Provinces
import takeover

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def keep_leaders(tag, gid, army_ids, cache):
    """Every army keeps a general and the army group a field marshal. Nobody is a field marshal at the start; the game
    promotes some later (in 1939 Rundstedt, Kluge, Kesselring), which takes them out of their armies. So: a promoted
    field marshal gets the army group, an army without a general (or led by a field marshal) gets the best free one.
    ARMY_IDS in priority order (the fast army first). Returns lines describing what changed."""
    lead = leaders_mod.load(tag, os.path.join(cache, "leaders.tsv"))
    by_addr = {_object_address(4713, l.id): l for l in lead}
    current = {a: by_addr.get(_pointer_at(_object_address(53, a) + 0x88)) for a in army_ids}
    used = {l.id for l in current.values() if l}
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
    lead = leaders_mod.load(tag, os.path.join(cache, "leaders.tsv"))
    group, others, marshal = organize.plan(m, provinces.controller, divs, tag, me, tgt, fast_units, lead, friend_ids)
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
                    help="infantry armies of the army group get battle plans (front line + offensive line, executed "
                         "at war; they build up the planning bonus); the controller then only moves the other divisions")
    ap.add_argument("--armies", action="store_true",
                    help="let the game form armies and pick generals (their buffs); fronts and plans stay blocked")
    ap.add_argument("--no-takeover", action="store_true", help="don't block the game AI's army commands")
    a = ap.parse_args()
    tag, target = a.tag.upper(), a.target.upper()

    m = mapdata.load(verbose=True)
    tags, ids = country_ids()
    if tag not in ids or target not in ids:
        raise SystemExit(f"unknown tag: {tag if tag not in ids else target}")
    me, tgt = ids[tag], ids[target]
    provinces = Provinces()
    snap = Snapshotter(os.path.join(REPO, ".cache", "controller_snap.tsv"))
    fast_units = {n for n, u in units.load().items() if u.fast}
    planner = Planner(m, me, fast_units)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log = Log(os.path.join(REPO, "recordings", f"controller_{tag}_{stamp}"))
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
    planned, executed = set(), False
    if a.plans and len(group_armies) > 1 and not a.dry_run:
        from hoi4ai.memory import Process
        provinces.refresh()
        infantry = group_armies[1:]  # the first army of the group is the fast army
        divs0 = snap.take()
        border = plans.fronts(Process(), me, tgt, provinces.controller, m)
        vps = {p: v for st in m.states.values() for p, v in st.victory_points.items()
               if provinces.controller[p] == tgt}
        if border and vps:
            armies = {aid: [d for d in divs0 if d.owner == tag and d.army == aid] for aid in infantry}
            for line in plans.draw(plans.make(m, armies, border, vps)):
                print(line)
                if "front" in line and "OK front line" in line:
                    planned.add(int(line.split()[1].rstrip(":")))
        else:
            print("battle plans: no border with", target, "found" if not border else "", "or no victory points")
    ticks = 0
    can_stop = send("bridge.stop")[0].startswith("FAIL usage: bridge.stop")
    print("stopping attacks with", "bridge.stop" if can_stop else "a move back to the division's own province")
    protected = set()
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
            if army_order and not a.dry_run and now.date() != leader_day:  # once a day: generals and field marshal
                leader_day = now.date()
                for line in keep_leaders(tag, group_id, army_order, os.path.join(REPO, ".cache")):
                    print(line)
            provinces.refresh()
            divs = snap.take()
            enemy_tags = enemies_of(tag)
            enemy_ids = [ids[t] for t in enemy_tags if t in ids]
            friend_ids = [ids[t] for t in friends_of(tag) if t in ids]
            at_war = target in enemy_tags
            if at_war and war_start is None:
                war_start = now
            if planned and at_war and not executed:
                for line in plans.execute(sorted(planned)):
                    print(line)
                executed = True
            # divisions of armies on a battle plan are the game's to move
            orders, rep = planner.plan(provinces.controller,
                                       [d for d in divs if not (d.owner == tag and d.army in planned)],
                                       tag, enemy_ids, tgt, at_war, friend_ids)
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
            for did, prov in orders.items():
                d = mine.get(did)
                if d is None or d.dest == prov or (d.province == prov and not d.moving):
                    continue
                kind = "attack" if provinces.controller[prov] in set(enemy_ids) else "post"
                cmds.append(f"bridge.move {did} {prov}")
                batch.append((d, prov, kind))
            for d in planner.stops:  # attacks to call off
                cmds.append(f"bridge.stop {d.id}" if can_stop else f"bridge.move {d.id} {d.province}")
                batch.append((d, d.province, "stop"))
            if cmds and not a.dry_run:
                for i in range(0, len(cmds), 40):
                    for (d, prov, kind), r in zip(batch[i:i + 40], send(*cmds[i:i + 40])):
                        ok = r.startswith("OK")
                        sent += ok
                        failed += not ok
                        log.move(now, d, prov, kind, "ok" if ok else r)
            ctrl = provinces.controller
            target_land = [p for p in m.provinces if m.is_land(p) and provinces.owner[p] == tgt]
            taken = sum(1 for p in target_land if ctrl[p] == me)
            capitulated = at_war and trigger(f"{target} = {{ has_capitulated = yes }}")
            row = dict(date=now.strftime("%Y-%m-%d %H:00"), at_war=int(at_war), enemies=" ".join(enemy_tags),
                       **rep, orders=len(orders), moves=sent, failed=failed,
                       target_provinces=len(target_land), taken=taken, capitulated=int(capitulated),
                       ms=int((time.time() - t0) * 1000))
            log.tick(row)
            print(f"{row['date']} war={row['at_war']} divs={rep['divisions']} front={rep['front']} "
                  f"fast={rep['fast']} attacks={rep.get('fast_attacks', 0)}+{rep.get('attacks', 0)} moves={sent} fail={failed} "
                  f"taken={taken}/{len(target_land)} short_of_supply={rep['short_of_supply']} ({row['ms']} ms)")
            if a.once:
                for did, prov in list(orders.items())[:15]:
                    d = mine.get(did)
                    print("  ", d, "->", prov)
                break
            if capitulated:
                days = (now - war_start).total_seconds() / 86400 if war_start else 0
                print(f"{target} capitulated on {row['date']}, {days:.1f} days after the war began")
                break
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
