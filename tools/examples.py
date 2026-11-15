"""Training examples from a cmdrec recording: each of the player's decisions with the state just before it.

    python examples.py RECORDING_DIR [--out FILE] [--tag GER]

Writes RECORDING_DIR/examples.jsonl (or FILE), one JSON object per decision:
  {"date": "1936-03-18 01:00", "source": "politics", "change": "changed", "auto": false,
   "action": {"kind": "focus", "slot": "current", "value": "GER_prioritize_economic_growth", ...},
   "state": {"country": {...country.tsv row of the day before...}, "focus": "...", "research": {...},
             "laws": {...}, "wargoals": ["POL:take_state"], "at_war": [...], "puppets": [...]}}
and prints a summary per source.

Decisions come from the per-system logs (the rows of the player's country that are not "start") and from the
player's own commands in command_record.tsv, grouped per in-game hour and class, each decoded: moves (division,
province path, target and its state), rail moves (division, target), assignments to armies (army, divisions),
battle-plan commands (hoi4ai.planrec.decode: front and offensive lines with their divisions and provinces), halts
(divisions), and the handles and lists the record holds for the rest. The record has no owner for some of the
player's commands (moves and rail moves of divisions trained during the game, handle 4713, and halts): they are
taken as the player's when a division they name is the player's in unit_snapshots.tsv ("owner_from":
"divisions"). Divisions are keyed as in analyze_snaps: the id for handle 51, "4713:ID" for later ones.
The tag is the record's "#mark player=".

"auto" marks rows the game makes by itself rather than the player: a focus or research finishing, a construction
done, its amount going down or a repair, a deployment arriving, a decision's state other than taken (active), an event with one option, an incoming offer appearing,
a trade deal of another country buying from the player or made while auto import is on (a deal the player made
then is counted as the game's: the logs can't tell them apart).
They stay in the file (a model may want to see them as context) but are not the player's choices.

A log row is dated when the recorder read it, which can be days after the change (3-13 in-game days in a busy game).
Where the record holds the command behind it (COUNTRY_AT_28: focus, research, decisions, construction, laws and
advisors, trade), the row takes the command's hour and keeps the log's date as "logged". The state is taken from
before the row's date, so it doesn't show the decision's own effect (for rows not re-dated, unless the read came late).
"""
import argparse
import datetime
import json
import os
import re
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge  # noqa: E402,F401  (puts hoi4bridge on the path)
from hoi4ai import planrec  # noqa: E402
from hoi4ai.clock import HOUR, Clock, parse  # noqa: E402

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"

# log: the columns that name a country the row is about (the player's row if any of them is the player)
LOGS = {
    "politics": ("country", None),
    "research": ("country", None),
    "construction": ("country", None),
    "production_lines": ("owner", None),
    "trade": ("exporter", "importer"),
    "logistics": ("country", None),
    "deployment": ("country", None),
    "division_templates": ("owner", None),
    "equipment_designs": ("owner", None),
    "decisions": ("country", None),
    "doctrines": ("country", None),
    "mios": ("country", None),
    "diplomacy": ("country", None),
    "naval": ("country", None),
    "air": ("country", None),
    "generals": ("country", None),
    "admirals": ("country", None),
    "intelligence": ("country", None),
    "occupation": ("country", None),
    "spirits": ("country", None),
    "market": ("seller", "buyer"),
    "events": ("country",),
}
NOISE = {"CAiStoreTotalWantedNrDivisionsCommand", "CClientPingCommand", "CHourlyTickCommand"}
SKIP = {"date", "change", "was"}

# Commands that hold their country's id at +0x28 (the record has no owner for them, or a wrong one), with the log
# and the rows of it each one makes. A log is read only every few in-game days (more when the game is busy), so such
# a row is dated by its command: the player's command of a matching class since the log's previous read.
_FOCUS = ("politics", lambda r: r.get("kind") == "focus" and r.get("slot") == "current"
          and r.get("change") in ("new", "changed"))
_DECISION = ("decisions", lambda r: r.get("change") == "taken" or r.get("value", "").startswith("active"))
_BUILD = ("construction", lambda r: r.get("change") == "new")
_IDEA = ("politics", lambda r: r.get("kind") in ("law", "advisor") and r.get("change") in ("new", "changed"))
COUNTRY_AT_28 = {
    "CSetNationalFocusCommand": _FOCUS,
    "CSetResearchCommand": ("research", lambda r: r.get("change") in ("new", "changed") and bool(r.get("tech"))),
    "CSelectDecisionCommand": _DECISION,
    "CSelectTargetedDecisionCommand": _DECISION,
    "CAddConstructionCommand": _BUILD,
    "CBuildRailwayCommand": _BUILD,
    "CAddIdeaCommand": _IDEA,
    "CReplaceIdeaCommand": _IDEA,
    "CCreateTradeCommand": ("trade", lambda r: r.get("change") == "new"),
}
SLACK = datetime.timedelta(days=1)     # a read is dated when it starts; the game moves on while it reads


def when(text):
    """datetime of a log date ("12:00, 1 January, 1936", "1936-01-02 07:00" or "1936-01-01")."""
    text = text.strip()
    if len(text) == 10:
        text += " 00:00"
    if text[10:13] == " 24":    # the day's end, as the game writes it
        d, _ = parse(text[:10] + " 23" + text[13:])
        return d and d + HOUR
    d, _ = parse(text)
    return d


def rows(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            p = line.rstrip("\n").split("\t")
            yield dict(zip(head, p + [""] * (len(head) - len(p))))


def auto(src, r, state, tag):
    ch = r.get("change")
    if src == "trade" and r.get("deal") != "setting":
        if r.get("exporter") == tag:
            return True     # another country buying from the player (the AI asks, the game accepts)
        if state.auto_import:
            return True     # the game's auto import (it changes deals by itself); a player's deal can hide here
    if src == "events":
        return r.get("options") == "1"     # one option: nothing to choose
    if ch in ("done", "deployed", "ignored"):
        return True
    if src == "politics" and r.get("kind") == "focus" and (r.get("slot") == "completed" or ch == "removed"):
        return True
    if src == "research" and ch == "changed" and not r.get("tech"):
        return True     # the slot emptied: the tech finished
    if src == "construction" and ch == "changed" and r.get("was", "").startswith("amount="):
        try:
            return int(r["amount"]) < int(r["was"].split("=", 1)[1].split(";")[0])
        except ValueError:
            return False
    if src == "construction" and r.get("repair"):
        return True     # a repair the game queues
    if src == "decisions" and ch != "taken" and not r.get("value", "").startswith("active"):
        return True     # became available, ignored, cooling down or gone; "taken" / "active" is the player taking it
    if src == "diplomacy" and r.get("kind") in ("incoming", "opinion"):
        return True
    return False


class State:
    """The player's state, replayed from the logs."""

    def __init__(self):
        self.country, self.focus, self.research, self.laws = {}, "", {}, {}
        self.wargoals, self.wars, self.puppets, self.auto_import = set(), set(), set(), False

    def apply(self, src, r):
        removed = r.get("change") == "removed"
        if src == "politics" and r.get("kind") == "focus" and r.get("slot") == "current":
            self.focus = "" if removed else r.get("value", "")
        elif src == "politics" and r.get("kind") == "law":
            self.laws[r.get("slot", "")] = r.get("value", "")
        elif src == "research" and r.get("slot"):     # (a "done" row has no slot)
            self.research[r.get("slot", "")] = "" if removed else r.get("tech", "")
        elif src == "diplomacy" and r.get("kind") == "wargoal":
            (self.wargoals.discard if removed else self.wargoals.add)(f"{r.get('partner', '')}:{r.get('key', '')}")
        elif src == "diplomacy" and r.get("kind") == "pact" and r.get("key") == "war_relation":
            (self.wars.discard if removed else self.wars.add)(r.get("partner", ""))
        elif src == "diplomacy" and r.get("kind") == "pact" and r.get("key") == "puppet":
            (self.puppets.discard if removed else self.puppets.add)(r.get("partner", ""))
        elif src == "trade" and r.get("resource") == "auto_import_enabled":
            self.auto_import = r.get("amount") == "yes"

    def snapshot(self):
        return {"country": dict(self.country), "focus": self.focus,
                "research": {k: v for k, v in sorted(self.research.items()) if v}, "laws": dict(self.laws),
                "wargoals": sorted(self.wargoals), "at_war": sorted(self.wars),
                "puppets": sorted(self.puppets)}


def _handles(text):
    """{offset: (type, id)} from the record's handles column (+0x38=51:30,+0x98=53:246)."""
    out = {}
    for h in text.split(",") if text not in ("", "-") else ():
        off, _, v = h.partition("=")
        t, _, i = v.partition(":")
        try:
            out[int(off.lstrip("+"), 16)] = (int(t), int(i))
        except ValueError:
            pass
    return out


def _lists(extra):
    return {int(m.group(1), 16): [x for x in m.group(2).split(",") if x]
            for m in re.finditer(r"L\+0x([0-9a-f]+)=\[([^\]]*)\]", extra or "")}


def division(handle, i):
    """A division's key, as analyze_snaps keeps it: its id for handle 51, "4713:ID" for one trained later."""
    return i if handle == 51 else f"{handle}:{i}"


def division_owners(path):
    """{division key: owner tag} from unit_snapshots.tsv (the last owner seen)."""
    out, handle = {}, "51"
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split("\t", 2)
            if p[0] == "#snap":
                handle = p[2].split("\t", 1)[0]
            elif len(p) > 1 and p[0].isdigit():
                out[division(int(handle), int(p[0]))] = p[1]
    return out


def owner_of(cls, handles, hexdump, extra, owners):
    """The owner of a command the record has no owner for, from the divisions it names (unit_snapshots)."""
    found = re.findall(r"\b(51|4713):(\d+)", handles + " " + extra)
    if cls in ("CMoveCommand", "CStrategicRedeploymentCommand") and len(hexdump) >= 0x80:
        found.append(struct.unpack_from("<ii", bytes.fromhex(hexdump[:0x80]), 0x38))
    for t, i in found:
        o = owners.get(division(int(t), int(i)))
        if o:
            return o
    return None


def decode(cls, handles, hexdump, extra, states):
    """What one command says (layouts: docs/game-layouts.md, hoi4ai/planrec.py): divisions, provinces, lines."""
    d = planrec.decode(cls, hexdump, extra)
    if d is not None:
        for k in ("points", "provinces"):
            if d.get(k):
                d[k + "_states"] = sorted({states.get(x, "?") for x in d[k]})
        return d
    h, L = _handles(handles), _lists(extra)
    b = bytes.fromhex(hexdump[:0x140])
    i32 = lambda off: struct.unpack_from("<i", b, off)[0] if len(b) >= off + 4 else None  # noqa: E731
    divs = lambda items: [division(*map(int, x.split(":")[:2])) for x in items if ":" in x]  # noqa: E731
    if cls == "CMoveCommand":
        path = [int(x) for x in L.get(0x40, [])]
        d = {"division": division(i32(0x38), i32(0x3c)), "path": path}
        if path:
            d["to"], d["to_state"] = path[-1], states.get(path[-1], "?")
        return d
    if cls == "CStrategicRedeploymentCommand":
        to = i32(0x40)
        return {"division": division(i32(0x38), i32(0x3c)), "to": to, "to_state": states.get(to, "?")}
    if cls in ("COrderAssignCommand", "COrderUnassignCommand"):
        army = next((i for t, i in h.values() if t == 53), None)
        return {"army": army, "divisions": divs(L.get(0x28, []))}
    if cls == "CCancelMovementCommand":
        return {"divisions": divs(L.get(0x28, []))}
    out = {}
    if h:
        out["handles"] = [f"{t}:{i}" for _, (t, i) in sorted(h.items())]
    for off, items in sorted(L.items()):
        out[f"list_{off:#x}"] = items
    return out


def player_of(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#mark\t") and "\tplayer=" in line:
                return line.rstrip("\n").split("player=", 1)[1]
    return None


def commands(path, tag, states, owners):
    """([(datetime, class, [what each says])] of TAG's commands, per in-game hour and class,
    [(datetime, class, country id)] of the COUNTRY_AT_28 commands, {event seq: country id}).
    A command the record has no owner for is TAG's when a division it names is TAG's (OWNERS: from unit_snapshots)."""
    from hoi4ai import eventrec
    clock, out, country_cmds, event_ids = Clock(), {}, [], {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            date = clock.feed(line)
            p = line.rstrip("\n").split("\t")
            if p[0].startswith("#") or len(p) < 8 or p[2] in NOISE or not date or not when(date):
                continue
            if p[2] == eventrec.CLASS:
                e = eventrec.parse(line)
                if e:
                    event_ids[e[0]] = e[3]
                continue
            if p[2] in COUNTRY_AT_28:
                if len(p[7]) >= 0x2c * 2:
                    cid = struct.unpack_from("<i", bytes.fromhex(p[7][0x50:0x58]))[0]
                    country_cmds.append((when(date), p[2], cid))
                continue
            extra = p[8] if len(p) > 8 else ""
            if p[3] == tag or (p[3] == "-" and owner_of(p[2], p[6], p[7], extra, owners) == tag):
                what = decode(p[2], p[6], p[7], extra, states)
                if p[3] == "-":
                    what["owner_from"] = "divisions"
                out.setdefault((when(date), p[2]), []).append(what)
    return [(d, c, w) for (d, c), w in sorted(out.items(), key=lambda x: x[0])], country_cmds, event_ids


def country_tags(event_ids, events_path):
    """{country id: tag}, offline: the record's event commands name the country by id, events.tsv by tag."""
    seen = {}
    if os.path.exists(events_path):
        for r in rows(events_path):
            cid = event_ids.get(int(r["seq"])) if r.get("seq", "").isdigit() else None
            if cid is not None:
                seen.setdefault(cid, Counter())[r["country"]] += 1
    return {cid: c.most_common(1)[0][0] for cid, c in seen.items()}


def retime(log_rows, cmds, polls):
    """Date the player's log rows by their commands. LOG_ROWS: [(datetime, source, row)]; CMDS: [(datetime, class)]
    of the player's COUNTRY_AT_28 commands; POLLS: {source: sorted read dates}. Returns {id(row): command datetime}:
    each row takes the earliest unused command of a matching class between the log's previous read (less SLACK)
    and the row."""
    import bisect
    out, used = {}, set()
    cmds = sorted(cmds)
    for d, src, r in sorted(log_rows, key=lambda x: x[0]):
        classes = {c for c, (s, test) in COUNTRY_AT_28.items() if s == src and test(r)}
        if not classes:
            continue
        reads = polls.get(src, [])
        i = bisect.bisect_left(reads, d)
        since = (reads[i - 1] if i else d - datetime.timedelta(days=30)) - SLACK
        for k, (t, c) in enumerate(cmds):
            if t > d:
                break
            if k not in used and c in classes and t >= since:
                used.add(k)
                out[id(r)] = t
                break
    return out


def provinces_to_states(game, load=None):
    """province id -> state name, from history/states/<id>-<name>.txt through the mods of LOAD (the recording's
    copy of dlc_load.json), else the ones active now (hoi4ai.gamefiles); empty without the game files."""
    from hoi4ai.gamefiles import GameFiles
    out = {}
    try:
        files = GameFiles(game or None, load=load if load and os.path.exists(load) else None)
        for fn, path in files.listdir("history/states", ".txt").items():
            name = os.path.splitext(os.path.basename(fn))[0].split("-", 1)[-1].strip()
            m = re.search(r"provinces\s*=\s*\{([^}]*)\}",
                          open(path, encoding="utf-8-sig", errors="replace").read())
            for pid in m.group(1).split() if m else ():
                out[int(pid)] = name
    except OSError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    ap.add_argument("--tag", help="the player's tag (default: the record's #mark player=)")
    ap.add_argument("--game", default=GAME, help="the game's folder (province -> state names)")
    a = ap.parse_args()
    rec = os.path.join(a.dir, "command_record.tsv")
    tag = a.tag or (player_of(rec) if os.path.exists(rec) else None)
    if not tag:
        sys.exit("no player tag: pass --tag")
    owners = division_owners(os.path.join(a.dir, "unit_snapshots.tsv"))
    states = provinces_to_states(a.game, os.path.join(a.dir, "dlc_load.json"))
    cmds, country_cmds, event_ids = commands(rec, tag, states, owners) if os.path.exists(rec) else ([], [], {})
    tags = country_tags(event_ids, os.path.join(a.dir, "events.tsv"))
    mine = [(t, c) for t, c, cid in country_cmds if tags.get(cid) == tag]

    events, log_rows, polls = [], [], {}     # events: (datetime, order, kind, source, row)
    for src, owners in LOGS.items():
        path = os.path.join(a.dir, src + ".tsv")
        if not os.path.exists(path):
            continue
        reads = set()
        for r in rows(path):
            d = when(r.get("date", ""))
            if d is not None:
                reads.add(d)
            if d is None or not any(r.get(o) == tag for o in owners if o):
                continue
            log_rows.append((d, src, r))
        polls[src] = sorted(reads)
    timed = retime([x for x in log_rows if x[2].get("change") != "start"], mine, polls)
    for d, src, r in log_rows:
        if id(r) in timed:
            r["_logged"] = d
            d = timed[id(r)]
        events.append((d, 1, "log", src, r))
    path = os.path.join(a.dir, "country.tsv")
    if os.path.exists(path):
        for r in rows(path):
            d = when(r["date"])
            if d is not None:     # read during that day: counts from the next day on
                events.append((d.replace(hour=23, minute=59), 0, "country", "country", r))
    path = os.path.join(a.dir, "plans.tsv")
    if os.path.exists(path):
        for r in rows(path):
            d = when(r["date"])
            if d is not None and r.get("army") not in ("-", ""):
                events.append((d, 1, "log", "plans", r))
    for d, cls, what in cmds:
        events.append((d, 1, "command", "command", {"class": cls, "count": len(what), "orders": what}))
    events.sort(key=lambda e: (e[0], e[1]))

    state, out, counts = State(), [], Counter()
    last_plan = {}
    for d, _, kind, src, r in events:
        if kind == "country":
            state.country = {k: v for k, v in r.items() if k != "date"}
            continue
        if src == "plans":
            key = r.get("army")
            body = {k: v for k, v in r.items() if k != "date"}
            if last_plan.get(key) == body:
                continue
            change = "new" if key not in last_plan else "changed"
            last_plan[key] = body
            out.append({"date": f"{d:%Y-%m-%d %H:%M}", "source": "plans", "change": change, "auto": False,
                        "action": {k: v for k, v in body.items() if v}, "state": state.snapshot()})
            counts[("plans", False)] += 1
            continue
        if kind == "command":
            out.append({"date": f"{d:%Y-%m-%d %H:%M}", "source": "command", "change": "issued", "auto": False,
                        "action": r, "state": state.snapshot()})
            counts[("command", False)] += 1
            continue
        if r.get("change") == "start":
            state.apply(src, r)
            continue
        logged = r.pop("_logged", None)
        is_auto = auto(src, r, state, tag) and not logged     # a row with its command is the player's
        e = {"date": f"{d:%Y-%m-%d %H:%M}", "source": src, "change": r.get("change", ""), "auto": is_auto,
             "action": {k: v for k, v in r.items() if k not in SKIP and v},
             "was": r.get("was", ""), "state": state.snapshot()}
        if logged:
            e["logged"] = f"{logged:%Y-%m-%d %H:%M}"   # the log's date; "date" is the command's
        out.append(e)
        counts[(src, is_auto)] += 1
        state.apply(src, r)

    dest = a.out or os.path.join(a.dir, "examples.jsonl")
    with open(dest, "w", encoding="utf-8") as f:
        for e in out:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"{tag}: {len(out)} examples -> {dest}")
    print(f"country ids from events: {len(tags)}; the player's country commands: {len(mine)}, "
          f"{len(timed)} matched to a log row (dated by the command)")
    print(f"{'source':20} {'player':>7} {'auto':>6}")
    for src in sorted({s for s, _ in counts}, key=lambda s: -counts[(s, False)]):
        print(f"{src:20} {counts[(src, False)]:7} {counts[(src, True)]:6}")


if __name__ == "__main__":
    main()
