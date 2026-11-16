"""The player's actions per minute (APM) from a cmdrec recording.

    python apm.py RECORDING_DIR [--gap-ms 400] [--out FILE] [--tag GER]

Writes RECORDING_DIR/apm.json (or FILE) and prints a summary. The record's `ms` column is real time (milliseconds
since recording began, never paused), so the minutes are real minutes of play, however fast or paused the game ran.

The player's commands are found as examples.py finds them: the owner column is the "#mark player=" tag, or "-" with
a division the player owns (unit_snapshots.tsv), or a country id at +0x28 / in the event command that is the
player's (tags from events.tsv). Noise classes (pings, the hourly tick, the AI's bookkeeping) are left out.

A click can send one command to every division it selected (a hundred COrderAssignCommand rows within a few
milliseconds), so counting rows would count the selection, not the player. An *action* is a run of the player's
commands with no gap longer than --gap-ms (default 400) between neighbours; its class is the class of its first
command and its size the number of commands. The summary gives actions per minute over the whole record, over the
minutes with any action ("active"), the busiest minute and the 50/90th percentile of active minutes, and the
actions and commands per class. In apm.json "minutes" is [{"minute", "actions", "commands", "date"}, ...] for the
minutes with any action ("date" the in-game date at its first action) and "actions" the list of
{"ms", "class", "commands"}.

Input level: when the folder has input.tsv (cmdrec --input-apm, off by default: counts of key and mouse presses per
second while HOI4 was focused, hoi4ai/inputrec.py) the summary also holds "input": presses per minute the same way
(total, active minutes, median, 90th percentile, the busiest minute, keys against clicks) and the per-minute series.
A press is one press (no merging). Both are on the record's clock (cmdrec hands the input log the tick at which it turned the
record on; the record's ms is GetTickCount64 minus that tick), so "minute" means the same minute in both, and "timeline"
holds each minute's actions, keys and clicks with the in-game date of its first action.

Limits: a command the player triggers that the record holds no owner for (and no division of theirs, no country
id) is not counted, and one the game makes for the player on its own (a focus finishing) is not told from a click.
Time spent thinking or on menus with no command (reading, the map, a pause) shows as a quiet minute.
"""
import argparse
import json
import os
import struct
import sys
from collections import Counter

import _bridge  # noqa: F401  (puts the repo on sys.path)

import examples
from hoi4ai import eventrec
from hoi4ai.clock import Clock


def player_commands(rec, tag):
    """[(ms, class, date)] of TAG's commands in the record folder REC, in order. See the module docstring."""
    path = os.path.join(rec, "command_record.tsv")
    owners = examples.division_owners(os.path.join(rec, "unit_snapshots.tsv"))
    clock = Clock()
    mine, unknown, event_ids = [], [], {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            date = clock.feed(line)
            p = line.rstrip("\n").split("\t")
            if p[0].startswith("#") or len(p) < 8 or p[2] in examples.NOISE:
                continue
            try:
                ms = int(p[1])
            except ValueError:
                continue
            if p[2] == eventrec.CLASS:
                e = eventrec.parse(line)
                if e:
                    event_ids[e[0]] = e[3]
                    unknown.append((ms, p[2], date, e[3]))
                continue
            if p[2] in examples.COUNTRY_AT_28:
                if len(p[7]) >= 0x2c * 2:
                    cid = struct.unpack_from("<i", bytes.fromhex(p[7][0x50:0x58]))[0]
                    unknown.append((ms, p[2], date, cid))
                continue
            extra = p[8] if len(p) > 8 else ""
            if p[3] == tag or (p[3] == "-" and examples.owner_of(p[2], p[6], p[7], extra, owners) == tag):
                mine.append((ms, p[2], date))
    tags = examples.country_tags(event_ids, os.path.join(rec, "events.tsv"))
    mine += [(ms, c, d) for ms, c, d, cid in unknown if tags.get(cid) == tag]
    return sorted(mine, key=lambda x: x[0])


def actions(cmds, gap_ms):
    """[{"ms", "class", "commands", "date"}]: runs of CMDS with no gap over GAP_MS between neighbours."""
    out = []
    for ms, cls, date in cmds:
        if out and ms - out[-1]["_last"] <= gap_ms:
            out[-1]["commands"] += 1
            out[-1]["_last"] = ms
        else:
            out.append({"ms": ms, "class": cls, "commands": 1, "date": date or "", "_last": ms})
    for a in out:
        del a["_last"]
    return out


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def summary(acts, total_ms):
    per = {}
    for a in acts:
        m = per.setdefault(a["ms"] // 60000, {"minute": a["ms"] // 60000, "actions": 0, "commands": 0, "date": a["date"]})
        m["actions"] += 1
        m["commands"] += a["commands"]
    minutes = [per[k] for k in sorted(per)]
    counts = [m["actions"] for m in minutes]
    return {
        "actions": len(acts), "commands": sum(a["commands"] for a in acts),
        "record_minutes": round(total_ms / 60000, 1), "active_minutes": len(minutes),
        "apm": round(len(acts) / (total_ms / 60000), 2) if total_ms else 0,
        "apm_active": round(sum(counts) / len(counts), 2) if counts else 0,
        "apm_median_active": pct(counts, 0.5), "apm_p90_active": pct(counts, 0.9),
        "peak": max(minutes, key=lambda m: m["actions"]) if minutes else None,
        "by_class": {c: {"actions": n, "commands": sum(a["commands"] for a in acts if a["class"] == c)}
                     for c, n in Counter(a["class"] for a in acts).most_common()},
        "minutes": minutes,
    }


def input_summary(path):
    """Presses per minute from input.tsv (second, keys, clicks), or None when the file is not there."""
    if not os.path.exists(path):
        return None
    per, keys, clicks, last = {}, 0, 0, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) == 3 and p[0].isdigit():
                m = per.setdefault(int(p[0]) // 60, [0, 0])
                m[0] += int(p[1])
                m[1] += int(p[2])
                keys, clicks, last = keys + int(p[1]), clicks + int(p[2]), max(last, int(p[0]))
    counts = [k + c for k, c in per.values()]
    total = keys + clicks
    minutes = (last + 1) / 60
    return {"presses": total, "keys": keys, "clicks": clicks, "recorded_minutes": round(minutes, 1),
            "active_minutes": len(per), "apm": round(total / minutes, 2) if total else 0,
            "apm_active": round(total / len(per), 2) if per else 0,
            "apm_median_active": pct(counts, 0.5), "apm_p90_active": pct(counts, 0.9),
            "peak": max(counts) if counts else 0,
            "minutes": [{"minute": m, "keys": v[0], "clicks": v[1]} for m, v in sorted(per.items())]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--gap-ms", type=int, default=400, help="commands this close together are one action")
    ap.add_argument("--out")
    ap.add_argument("--tag", help="the player's tag (default: the record's #mark player=)")
    a = ap.parse_args()
    path = os.path.join(a.dir, "command_record.tsv")
    tag = a.tag or examples.player_of(path)
    if not tag or tag == "none":
        sys.exit("no player in the record (observer mode?): give --tag")
    cmds = player_commands(a.dir, tag)
    if not cmds:
        sys.exit(f"no commands of {tag} in {path}")
    ms0, ms1 = cmds[0][0], cmds[-1][0]
    acts = actions(cmds, a.gap_ms)      # ms stays the record's clock, as input.tsv's seconds are
    s = summary(acts, ms1 - ms0)
    s.update(player=tag, gap_ms=a.gap_ms, actions_list=[{k: x[k] for k in ("ms", "class", "commands")} for x in acts])
    s["input"] = input_summary(os.path.join(a.dir, "input.tsv"))
    if s["input"]:
        by = {m["minute"]: {"minute": m["minute"], "actions": m["actions"], "keys": 0, "clicks": 0, "date": m["date"]}
              for m in s["minutes"]}
        for m in s["input"]["minutes"]:
            t = by.setdefault(m["minute"], {"minute": m["minute"], "actions": 0, "keys": 0, "clicks": 0, "date": ""})
            t["keys"], t["clicks"] = m["keys"], m["clicks"]
        s["timeline"] = [by[k] for k in sorted(by)]
    out = a.out or os.path.join(a.dir, "apm.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(s, f)
    print(f"{tag}: {s['commands']} commands -> {s['actions']} actions over {s['record_minutes']} min "
          f"({s['active_minutes']} active)")
    print(f"APM {s['apm']} overall, {s['apm_active']} over active minutes (median {s['apm_median_active']}, "
          f"p90 {s['apm_p90_active']}); busiest minute {s['peak']['actions']} actions at {s['peak']['date']}")
    for c, v in list(s["by_class"].items())[:12]:
        print(f"  {c:40s} {v['actions']:6d} actions {v['commands']:7d} commands")
    i = s["input"]
    if i:
        print(f"input: {i['presses']} presses ({i['keys']} keys, {i['clicks']} clicks) in {i['recorded_minutes']} min: "
              f"{i['apm']} per minute, {i['apm_active']} over active minutes (median {i['apm_median_active']}, "
              f"p90 {i['apm_p90_active']}, busiest {i['peak']})")
    print("wrote", out)


if __name__ == "__main__":
    main()
