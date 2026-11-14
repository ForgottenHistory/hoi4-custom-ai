"""Navies: each country's task forces (fleet, ships, mission, regions, engagement, repair) and its admirals.

NavalLog(path, admiral_path, names, tags, others).step(date, lines) writes to naval.tsv:
  date  change  country  task_force  name  fleet  ships  mission  regions  engagement  repair  exercise  home_base
  admiral  auto_reinforce  auto_split  composition  detached  location  was
task_force the task force's id ("51:ID" for the starting ones, "61:ID" for ones made by split, balance or detach), fleet its fleet (handle 61 id and name, e.g. "1 Kriegsmarine"), ships how
many of each ship type it has (e.g. "destroyer:6,light_cruiser:2"), mission hold / patrol / strike_force /
convoy_raiding / convoy_escort / mines_planting / mines_sweeping / training / reserve_fleet / naval_invasion_support,
regions the strategic regions of the fleet's mission (comma separated), engagement and repair the task force's
settings (do_not_engage / low_risk / medium_risk / high_risk / always; never / low / medium / high: when it returns
for repairs), exercise until_max_xp when training stops at max experience (else empty), home_base the fleet's home
port (a province, e.g. 241 Wilhelmshaven; automated_homebase=yes stays after the player picks one), admiral the fleet's admiral (handle 4713 id
and name), auto_reinforce and auto_split yes when on, composition the composition editor's requirements (e.g. "submarine:0";
sub_units N is units.order()[N - 1], roles dropped), detached the activity and task force a detached group belongs
to (e.g. "reinforcing 61:682"), location the province it is in (written, not compared). change: start (the first read of a country), new
(a task force made, split off, or deployed), changed, removed (merged, deleted, or sunk). was: what changed, as field=old.

and to admirals.tsv:
  date  change  country  admiral  name  skill  attack  defense  maneuvering  coordination  traits  fleet  was
traits the admiral's traits in the game's order, fleet the fleet it commands (id and name, empty when unassigned).

Reads at the first call and each new in-game day: TAGS (the recorded countries) each time, OTHERS() (a callable, e.g.
MioLog's producing countries) every FULL_DAYS days, and TAGS at once after a naval command in the record LINES whose
owner column is "-" (the player's; the AI's have an owner offset). Admirals are found with a memory scan (CNavyLeader objects) at the first read and
every RESCAN_DAYS days.

Read from a country's save-format block: fleet={ id={ id=N type=61 } name leader={ id=A type=4713 } task_force={
id={ id=T type=51|61 } name location mission={ mission navy_engagement_rule stop_training_at_max_xp } repair_mode ship={
definition } auto_reinforcement repair_split repair_parent detached_activity ai_taskforce_composition={ requirements={ sub_units
amount } } } strategic_region={ R... } home_base automated_homebase }. mission 1 patrol, 2 strike force, 3 convoy
raiding, 7 training seen; the others follow the game's order. navy_engagement_rule 0-4 as the buttons (2 medium
risk the default), repair_mode 0-3 as the buttons (3 high the default). An admiral: bridge.write 4713 ID (name skill traits
attack_skill defense_skill maneuvering_skill coordination_skill); its country id at +0x120 of the CNavyLeader.

The player's commands (in command_record.tsv; the owner column is empty, the AI uses the same ones):
  CNavalMissionSetTypeCommand     task forces list +0x28 (count +0x30), mission +0x40, stop at max experience +0x41
  CNavalMissionAddRegionCommand   fleet 61:ID +0x28, strategic region +0x30
  CNavalMissionMoveCommand        task forces list +0x28
  CSetNavyEngagementCommand       task forces list +0x28, rule +0x40
  CNavyRepairModeCommand          task forces list +0x28
  CReorganizeShipsCommand         splitting ships off into a new task force
  CMergeNaviesCommand             task forces list +0x28 (merged)
  CSetFleetLeaderCommand          admiral 4713:ID +0x28, fleet 61:ID +0x30
  CSetFleetHomeBaseCommand        fleet 61:ID +0x28, province +0x30
  CSetAsReserveFleetCommand       making a fleet the reserve fleet
  CLearnTraitCommand              admiral 4713:ID +0x28 (as for generals)
  CSetTaskForceAutoReinforcementCommand  task forces list +0x28, +0x40 int (byte 0 on)
  CNavyRepairModeCommand also comes with the automatic split button (no other command does)
  CSetTaskForceCompositionRequirementsCommand  the composition editor
  CDeleteUnitCommand             task force 61:ID +0x28 (deleting it)
"""
import os
import re

from hoi4bridge import send

FULL_DAYS = 7
RESCAN_DAYS = 7
LEADER = 4713
HEADER = ("date\tchange\tcountry\ttask_force\tname\tfleet\tships\tmission\tregions\tengagement\trepair\texercise"
          "\thome_base\tadmiral\tauto_reinforce\tauto_split\tcomposition\tdetached\tlocation\twas")
FIELDS = ("name", "fleet", "ships", "mission", "regions", "engagement", "repair", "exercise", "home_base", "admiral",
          "auto_reinforce", "auto_split", "composition", "detached", "location")
COMPARE = FIELDS[:-1]
ADMIRAL_HEADER = ("date\tchange\tcountry\tadmiral\tname\tskill\tattack\tdefense\tmaneuvering\tcoordination\ttraits"
                  "\tfleet\twas")
ADMIRAL_FIELDS = ("name", "skill", "attack", "defense", "maneuvering", "coordination", "traits", "fleet")
COMMANDS = ("CNavalMissionSetTypeCommand", "CNavalMissionAddRegionCommand", "CNavalMissionMoveCommand",
            "CSetNavyEngagementCommand", "CNavyRepairModeCommand", "CReorganizeShipsCommand", "CMergeNaviesCommand",
            "CSetFleetLeaderCommand", "CSetFleetHomeBaseCommand", "CSetAsReserveFleetCommand", "CLearnTraitCommand",
            "CSetTaskForceAutoReinforcementCommand", "CSetTaskForceCompositionRequirementsCommand", "CDeleteUnitCommand")
MISSIONS = ("hold", "patrol", "strike_force", "convoy_raiding", "convoy_escort", "mines_planting", "mines_sweeping",
            "training", "reserve_fleet", "naval_invasion_support")
ENGAGEMENTS = ("do_not_engage", "low_risk", "medium_risk", "high_risk", "always")      # navy_engagement_rule
REPAIRS = ("never", "low", "medium", "high")                                          # repair_mode
CLASS = "CNavyLeader"
_FLEET = re.compile(r"\n\t*fleet=\{\s*id=\{ id=(\d+) type=61 \}")
_TASK = re.compile(r"\n\t*task_force=\{\s*id=\{ id=(\d+) type=(51|61) \}")


def _block(text, i):
    """The text of the { } block whose opening brace is at or after I."""
    i = text.find("{", i)
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i:j + 1]
    return text[i:]


def _get(text, key):
    m = re.search(r"\n\t*" + key + r"=(\"[^\"]*\"|[^\s{}]*)", text)
    return m.group(1).strip('"') if m else ""


def _name(names, v):
    return names[int(v)] if v.isdigit() and int(v) < len(names) else v


def _top(blk):
    """BLK without its nested { } blocks' contents past depth 1 (the block's own fields)."""
    out, depth = [], 0
    for ch in blk:
        if ch == "{":
            depth += 1
        if depth <= 1 or ch == "\n":
            out.append(ch)
        if ch == "}":
            depth -= 1
    return "".join(out)


_UNITS = []


def _units():
    if not _UNITS:
        from hoi4ai.units import order
        _UNITS.extend(order())
    return _UNITS


def composition(tb):
    """The composition editor's requirements of task force block TB, e.g. "destroyer:6,heavy_cruiser:1"."""
    i = tb.find("ai_taskforce_composition={")
    req = _block(tb, tb.find("requirements={", i)) if i >= 0 else ""
    lists = {k: (re.search(r"\n\t*" + k + r"=\{([^}]*)\}", req) or [None, ""])[1].split()
             for k in ("sub_units", "amount")}
    return ",".join(f"{_name([''] + _units(), u)}:{a}" for u, a in zip(lists["sub_units"], lists["amount"]))


def task_forces(text):
    """{task force "type:id": {field: value}} of one save-format country block (admiral: its id only)."""
    out = {}
    for f in _FLEET.finditer(text):
        fb = _block(text, f.start() + 1)
        tfs = [(m, _block(fb, m.start() + 1)) for m in _TASK.finditer(fb)]
        own = fb
        for _, tb in tfs:
            own = own.replace(tb, "{}")
        regions = re.search(r"\n\t*strategic_region=\{([^}]*)\}", own)
        leader = re.search(r"\n\t*leader=\{ id=(\d+) type=4713 \}", own)
        fleet = f"{f.group(1)} {_get(own, 'name')}".strip()
        for m, tb in tfs:
            mis = _block(tb, tb.find("mission={")) if "mission={" in tb else ""
            ships = {}
            for d in re.findall(r"\n\t*definition=(\w+)", tb):
                ships[d] = ships.get(d, 0) + 1
            mission = _get(mis, "mission")
            top = _top(tb)
            parent = re.search(r"\n\t*repair_parent=\{ id=(\d+) type=(\d+) \}", tb)
            detached = " ".join(filter(None, (_get(top, "detached_activity"),
                                              f"{parent.group(2)}:{parent.group(1)}" if parent else "")))
            out[f"{m.group(2)}:{m.group(1)}"] = {
                "name": _get(_top(tb), "name"), "fleet": fleet,
                "ships": ",".join(f"{k}:{v}" for k, v in sorted(ships.items())),
                "mission": _name(MISSIONS, mission),
                "regions": ",".join(regions.group(1).split()) if regions else "",
                "engagement": _name(ENGAGEMENTS, _get(mis, "navy_engagement_rule")),
                "repair": _name(REPAIRS, _get(_top(tb), "repair_mode")),
                "exercise": "until_max_xp" if re.search(r"\n\t*stop_training_at_max_xp=yes", mis) else "",
                "home_base": _get(own, "home_base"),
                "admiral": leader.group(1) if leader else "",
                "auto_reinforce": "yes" if re.search(r"\n\t*auto_reinforcement=yes", top) else "",
                "auto_split": "yes" if re.search(r"\n\t*repair_split=yes", top) else "",
                "composition": composition(tb), "detached": detached,
                "location": _get(top, "location")}
    return out


def admiral(lines):
    """{field: value} of an admiral from its bridge.write reply."""
    text = "\n" + "\n".join(lines)
    traits = re.search(r"\n\t*traits=\{([^}]*)\}", text)
    return {"name": _get(text, "name"), "skill": _get(text, "skill"), "attack": _get(text, "attack_skill"),
            "defense": _get(text, "defense_skill"), "maneuvering": _get(text, "maneuvering_skill"),
            "coordination": _get(text, "coordination_skill"),
            "traits": ",".join(traits.group(1).split()) if traits else "", "fleet": ""}


def parse(line):
    """A record line -> the class of a player's naval command (owner column "-"), or None."""
    p = line.split("\t", 4)
    return p[2] if len(p) > 4 and p[2] in COMMANDS and p[3] == "-" else None


class NavalLog:
    def __init__(self, path, admiral_path=None, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        self.proc = Process()
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {task force id: {field: value}}
        self.held_adm = {}          # tag -> {admiral id: {field: value}}
        self.admirals = {}          # admiral id -> CNavyLeader address
        self.started = False
        self.day, self.days = None, 0
        self.f = self._open(path, HEADER)
        self.fa = self._open(admiral_path or os.path.join(os.path.dirname(path), "admirals.tsv"), ADMIRAL_HEADER)

    @staticmethod
    def _open(path, header):
        new = not os.path.exists(path)
        f = open(path, "a", encoding="utf-8")
        if new:
            f.write(header + "\n")
        return f

    def find(self):
        p, found = self.proc, {}
        for a in p.find_objects(CLASS):
            if p.i32(a + 8) == LEADER and p.class_of(a) == CLASS and p.i32(a + 0x120) >= 0:
                found.setdefault(p.i32(a + 0xc), []).append(a)
        self.admirals = {}
        for lid, a in found.items():
            if len(a) > 1:          # stale copies too: the handle names the live one
                m = re.search(r"@0x([0-9a-fA-F]+)", send(f"bridge.object {LEADER} {lid}")[0])
                a = [int(m.group(1), 16)] if m else []
            if a:
                self.admirals[lid] = a[0]

    def _admirals(self, tag, fleets):
        p, ids = self.proc, []
        for lid, a in list(self.admirals.items()):
            if p.i32(a + 8) != LEADER or p.i32(a + 0xc) != lid:
                del self.admirals[lid]
            elif self.names.tag(p.i32(a + 0x120)) == tag:
                ids.append(lid)
        out = {}
        for lid, r in zip(ids, _split(send(*[f"bridge.write {LEADER} {i}" for i in ids])) if ids else ()):
            if r and r[0].startswith("OK " + CLASS):
                out[lid] = admiral(r)
                out[lid]["fleet"] = fleets.get(str(lid), "")
        return out

    def read(self, tag):
        r = send(f"bridge.country {tag}")
        tf = task_forces("\n".join(r)) if r and r[0].startswith("OK") else {}
        fleets = {v["admiral"]: v["fleet"] for v in tf.values() if v["admiral"]}
        adm = self._admirals(tag, fleets)
        for v in tf.values():
            if v["admiral"]:
                v["admiral"] = f"{v['admiral']} {adm[int(v['admiral'])]['name']}" if int(v["admiral"]) in adm \
                    else v["admiral"]
        return tf, adm

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        new_day = day != self.day
        if new_day:
            self.days += 1
        cmds = [c for c in map(parse, lines) if c]
        if not self.started or (new_day and self.days % RESCAN_DAYS == 0):
            self.find()
        todo = list(self.tags) if cmds else []
        if new_day:
            todo += self.tags
            if not self.started or self.days % FULL_DAYS == 0:
                todo += list(self.others() or ())
        self.day = day
        rows, arows = [], []
        for tag in dict.fromkeys(todo):
            now, adm = self.read(tag)
            rows += _diff(tag, now, self.held.get(tag), FIELDS, COMPARE)
            arows += _diff(tag, adm, self.held_adm.get(tag), ADMIRAL_FIELDS, ADMIRAL_FIELDS)
            self.held[tag], self.held_adm[tag] = now, adm
        self.started = True
        for f, rs in ((self.f, rows), (self.fa, arows)):
            for row in rs:
                f.write("\t".join((date,) + row) + "\n")
            f.flush()
        return len(rows) + len(arows)

    def close(self):
        self.f.close()
        self.fa.close()


def _split(lines):
    """The replies of a batch of bridge calls, one list of lines per call."""
    out = []
    for ln in lines:
        if ln.startswith(("OK ", "FAIL", "ERROR")) or not out:
            out.append([ln])
        else:
            out[-1].append(ln)
    return out


def _diff(tag, now, last, fields, compare):
    rows = []
    for i, v in sorted(now.items()):
        old = None if last is None else last.get(i)
        if old is None:
            rows.append(("start" if last is None else "new", tag, str(i)) + tuple(v[f] for f in fields) + ("",))
        elif any(old[f] != v[f] for f in compare):
            was = ";".join(f"{f}={old[f]}" for f in compare if old[f] != v[f])
            rows.append(("changed", tag, str(i)) + tuple(v[f] for f in fields) + (was,))
    for i, v in sorted((last or {}).items()):
        if i not in now:
            rows.append(("removed", tag, str(i)) + tuple(v[f] for f in fields) + ("",))
    return rows
