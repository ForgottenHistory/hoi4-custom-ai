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
import datetime
import os
import re
import time

import _bridge  # noqa: F401
from hoi4bridge import BridgeError, query, send, trigger
from hoi4ai import leaders as leaders_mod, mapdata, organize, units
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


def build_structure(m, provinces, divs, tag, me, tgt, fast_units, friend_ids, cache):
    """Build the armies, generals and the army group (hoi4ai/organize.py) with bridge.army. Returns a summary."""
    lead = leaders_mod.load(tag, os.path.join(cache, "leaders.tsv"))
    group, others, marshal = organize.plan(m, provinces.controller, divs, tag, me, tgt, fast_units, lead, friend_ids)
    lines, group_ids = [], []
    for army in group + others:
        r = send("bridge.army create " + " ".join(str(d.id) for d in army.divisions))[0]
        if not r.startswith("OK"):
            lines.append(f"{army.role}: {r}")
            continue
        aid = int(r.split()[2])
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
            fm = ""
            if marshal:
                lr = send(f"bridge.army leader {gid} {marshal.id}")[0]
                fm = f", field marshal {marshal.name} (attack {marshal.attack})" if lr.startswith("OK") else f", field marshal: {lr}"
            lines.append(f"army group {gid} of {len(group_ids)} armies{fm}")
        else:
            lines.append("army group: " + r)
    return lines


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
            "support_points", "fast_attacks", "fast_attacks_divisions", "attacks", "attacks_divisions", "retreats",
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
        for line in build_structure(m, provinces, snap.take(), tag, me, tgt, fast_units, friend_ids0,
                                    os.path.join(REPO, ".cache")):
            print("  " + line)
    protected = set()
    war_start = None
    last = None
    print(f"running {tag} against {target}, a plan every {a.hours} in-game hours; logs in {log.folder}")
    try:
        while True:
            try:
                now = game_time(tag)
            except (BridgeError, ValueError) as e:
                print("date:", e)
                time.sleep(2)
                continue
            if last and now - last < datetime.timedelta(hours=a.hours):
                time.sleep(0.25)
                continue
            last = now
            t0 = time.time()
            provinces.refresh()
            divs = snap.take()
            enemy_tags = enemies_of(tag)
            enemy_ids = [ids[t] for t in enemy_tags if t in ids]
            friend_ids = [ids[t] for t in friends_of(tag) if t in ids]
            at_war = target in enemy_tags
            if at_war and war_start is None:
                war_start = now
            orders, rep = planner.plan(provinces.controller, divs, tag, enemy_ids, tgt, at_war, friend_ids)
            mine = {d.id: d for d in divs if d.owner == tag}
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
