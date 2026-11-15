"""Generals and field marshals: each country's army leaders, their skills, traits and the army they command.

GeneralLog(path, names, tags, others).step(date, lines) writes to generals.tsv:
  date  change  country  leader  name  role  attack  defense  planning  logistics  traits  army  was
leader the leader's id (handle 4713), role general / field_marshal, the skills as the game shows them (with
modifiers), traits every trait the leader has in the game's order (personality, terrain and learned ones, and passing
ones like recently_promoted), army the army or army group it commands (its handle 53 id and name, e.g. "409 Army 1";
empty when unassigned). change: start (the first read of a country), new (recruited), changed, removed (dismissed,
or killed). was: what changed, as field=old (e.g. "role=general", "army=409 Army 1"; traits: the old list).

Reads at the first call and each new in-game day: TAGS (the recorded countries) each time, OTHERS() (a callable, e.g.
MioLog's producing countries) every FULL_DAYS days, and at once the country of every leader command in the record
LINES. Leaders are found with a memory scan (CArmyLeader objects, ~10 s) at the first read and every RESCAN_DAYS
days, and again after a command that makes one; a leader a command names that isn't known yet is looked up by its
handle.

Memory (CArmyLeader, the object of handle 4713:ID): handle type +0x8, id +0xc, key std::string +0x20, name +0x40,
country id +0x120, can-be-field-marshal byte +0xde8, current role +0xe7c (0 field marshal, 1 general; with neither
the flag nor role 1 the leader isn't usable and is left out, as in hoi4ai/leaders.py), skills attack +0xf58, defense
+0xf68, planning +0xf78, logistics +0xf88. Traits: a vector (data, int count) of CUnitLeaderTrait* at +0xdc8, the
trait key a std::string at +0x10 of each (+0xdf8 holds the traits that can still be learned, +0xe10 one more).
An army (COrdersGroup) or army group (CArmyGroup), bridge.object 53 ID: its CArmyLeader* at +0x88 (null without one).
The armies of a country are the type-53 ids with a name in its save-format block (orders_group={ id={ id=N type=53 }
name=... }).

The player's commands (in command_record.tsv):
  CSetArmyLeaderCommand      leader 4713:ID +0x28 (type 0 and id 0: taking the leader away), army 53:ID +0x30; owner
                             column the country
  CPromoteUnitLeaderCommand  leader 4713:ID +0x28 (making a general a field marshal)
  CLearnTraitCommand         leader 4713:ID +0x28, CUnitLeaderTrait* +0x30 (picking a trait)
  CUpgradeDivisionOfficerCommand   division 51:ID +0x28; owner column the country (recruiting: Officers, Promote on a
                             division's officer makes it a general)
  CCreateUnitLeaderCommand   making a leader (seen from the AI)
"""
import os
import re

from hoi4ai import shared
from hoi4bridge import send

FULL_DAYS = 7
RESCAN_DAYS = 7
LEADER, ARMY = 4713, 53
HEADER = "date\tchange\tcountry\tleader\tname\trole\tattack\tdefense\tplanning\tlogistics\ttraits\tarmy\twas"
FIELDS = ("name", "role", "attack", "defense", "planning", "logistics", "traits", "army")
NEW = ("CUpgradeDivisionOfficerCommand", "CCreateUnitLeaderCommand")      # a leader made: scan for it
COMMANDS = ("CSetArmyLeaderCommand", "CPromoteUnitLeaderCommand", "CLearnTraitCommand") + NEW
CLASS = "CArmyLeader"
_GROUP = re.compile(r"\w+=\{\s*id=\{ id=(\d+) type=53 \}\s*name=\"([^\"]*)\"")


def parse(line):
    """A record line -> (class, owner tag or "", leader id or None) of a leader command, or None."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] not in COMMANDS:
        return None
    b = bytes.fromhex(p[7])
    lid = None
    if len(b) >= 0x30 and int.from_bytes(b[0x28:0x2c], "little") == LEADER:
        lid = int.from_bytes(b[0x2c:0x30], "little")
    return p[2], p[3] if p[3] not in ("", "-") else "", lid


class GeneralLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        shared.want(CLASS)
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        from hoi4ai.templates import _string
        self.proc = Process()
        self._string = _string
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.leaders = {}           # leader id -> CArmyLeader address
        self.owner = {}             # leader id -> tag
        self.held = {}              # tag -> {leader id: {field: value}}
        self.trait = {}             # CUnitLeaderTrait address -> key
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def _valid(self, a, lid=None):
        p = self.proc
        return p.i32(a + 8) == LEADER and (lid is None or p.i32(a + 0xc) == lid) and p.class_of(a) == CLASS

    def find(self):
        found = {}
        for a in shared.objects(CLASS):
            if self._valid(a):
                lid = self.proc.i32(a + 0xc)
                if 0 < lid and self.proc.i32(a + 0x120) >= 0:
                    found.setdefault(lid, []).append(a)
        self.leaders = {lid: a[0] for lid, a in found.items() if len(a) == 1}
        for lid in (lid for lid, a in found.items() if len(a) > 1):   # stale copies too: the handle names the live one
            self.lookup(lid)

    def lookup(self, lid):
        r = send(f"bridge.object {LEADER} {lid}")[0]
        m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK " + CLASS) else None
        if m:
            self.leaders[lid] = int(m.group(1), 16)
            self.owner[lid] = self.names.tag(self.proc.i32(self.leaders[lid] + 0x120))
        return self.owner.get(lid)

    def _traits(self, a):
        p, out = self.proc, []
        v, n = p.u64(a + 0xdc8), p.i32(a + 0xdc8 + 8)
        for k in range(max(0, min(n, 40))):
            t = p.u64(v + 8 * k)
            if t not in self.trait:
                self.trait[t] = self._string(p, t + 0x10) if p.class_of(t) == "CUnitLeaderTrait" else ""
            if self.trait[t]:
                out.append(self.trait[t])
        return ",".join(out)

    def armies(self, tag):
        """{leader id: "ID name"} of the armies and army groups of TAG that have a leader."""
        r = shared.country(tag)
        text = "\n".join(r) if r and r[0].startswith("OK") else ""
        groups = dict.fromkeys(_GROUP.findall(text))
        out = {}
        for (gid, name), resp in zip(groups, send(*[f"bridge.object {ARMY} {g}" for g, _ in groups]) if groups else ()):
            m = re.search(r"@0x([0-9a-fA-F]+)", resp) if resp.startswith("OK") else None
            if not m:
                continue
            ldr = self.proc.u64(int(m.group(1), 16) + 0x88)
            if ldr and self._valid(ldr):
                out[self.proc.i32(ldr + 0xc)] = f"{gid} {name}"
        return out

    def _groups(self):
        """{tag: [(leader id, address)]} of the known leaders (dropping those no longer there)."""
        p, out = self.proc, {}
        for lid, a in list(self.leaders.items()):
            if not self._valid(a, lid):
                del self.leaders[lid]
                continue
            owner = self.names.tag(p.i32(a + 0x120))
            self.owner[lid] = owner
            out.setdefault(owner, []).append((lid, a))
        return out

    def read(self, tag, groups=None):
        p, out = self.proc, {}
        army = self.armies(tag)
        for lid, a in (groups if groups is not None else self._groups()).get(tag, ()):
            marshal, role = p.i32(a + 0xde8) & 0xff, p.i32(a + 0xe7c)
            if not marshal and role != 1:
                continue
            out[lid] = {"name": self._string(p, a + 0x40), "role": "general" if role == 1 else "field_marshal",
                        "attack": str(p.i32(a + 0xf58)), "defense": str(p.i32(a + 0xf68)),
                        "planning": str(p.i32(a + 0xf78)), "logistics": str(p.i32(a + 0xf88)),
                        "traits": self._traits(a), "army": army.get(lid, "")}
        return out

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        new_day = day != self.day
        if new_day:
            self.days += 1
        if not self.started or (new_day and self.days % RESCAN_DAYS == 0):
            self.find()
        todo = []
        for cls, owner, lid in filter(None, map(parse, lines)):
            if owner:
                todo.append(owner)
            if lid:
                tag = self.owner.get(lid) if lid in self.leaders else self.lookup(lid)
                if tag:
                    todo.append(tag)
            elif cls in NEW:
                if self.started:
                    self.find()
                todo += self.tags
        if new_day:
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
        groups = self._groups() if todo else {}
        for tag in dict.fromkeys(todo):
            now, last = self.read(tag, groups), self.held.get(tag)
            for lid, v in sorted(now.items()):
                old = None if last is None else last.get(lid)
                if last is None or old is None:
                    rows.append(("start" if last is None else "new", tag, str(lid)) + tuple(v[f] for f in FIELDS)
                                + ("",))
                elif old != v:
                    was = ";".join(f"{f}={old[f]}" for f in FIELDS if old[f] != v[f])
                    rows.append(("changed", tag, str(lid)) + tuple(v[f] for f in FIELDS) + (was,))
            for lid, v in sorted((last or {}).items()):
                if lid not in now:
                    rows.append(("removed", tag, str(lid)) + tuple(v[f] for f in FIELDS) + ("",))
            self.held[tag] = now
        self.started = True
        for row in rows:
            self.f.write("\t".join((date,) + row) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
