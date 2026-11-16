"""Battle plans in recordings: what the commands that make them say, and the plans as the game holds them.

Three parts:
  RULES / install_rules()  bridge.cmdrec rules so the record also holds the lists behind the battle-plan commands
                           (divisions, path points, fortification provinces)
  decode(cls, hex, extra)  one command_record.tsv row of a battle-plan class -> a dict of what it says
  snapshot(proc, armies)   every line of the given armies, read from memory -> rows for plans.tsv

Layouts (found 2026-11-14 by recording a player drawing each kind of line, ARG vs PAR):

Commands (the army is a handle {53, id} at +0x28 of each, so its id is the int at +0x2c):
  COrderGroupCommand            create an army: +0x30 division handles, +0x48 theatre handle (67)
  COrderNewRootCommand          front line: +0x30 divisions, +0x48 CFront handle (66), +0x50 s, +0x58 / +0x60 start /
                                end along the CFront's sections (int64, 100000 = all of it)
  COrderNewFrontCommand         offensive line: +0x38 divisions, +0x50 province points (the last is the target),
                                +0x68 id of the front line it belongs to
  COrderEditRootCommand         front line dragged: +0x30 line id, +0x38 / +0x40 its new bounds (int64; which is
                                start and which end is not known yet: shortening one end gave 100000 / 16666)
  COrderSetPathCommand          offensive line redrawn: +0x30 line id, +0x38 province points
  COrderNewFallbackCommand      fortification line: +0x48 provinces
  COrderExecuteCommand          +0x34 1 execute, 0 stop
  COrderDeleteCommand           +0x30 line id
  COrderDeleteAllCommand        every line of the army
  CSetOrderGroupCohesionTypeCommand  +0x30 0 flexible, 1 balanced, 2 rigid, 3 immobile
  COrderSetInvasionSourceCommand     naval invasion: +0x38 province, +0x40 divisions
  CAddNavalInvasionTargetCommand     +0x30 line id, +0x34 target province
  CRemoveNavalInvasionTargetCommand  +0x30 line id, +0x34 target province
  COrderSetParadropSourceCommand     +0x34 the airbase's province; +0x38 vector pointer, +0x40 its count (1 in the 489
                                     orders of the meme_ger recording: the divisions, as handles? NOT verified live)
  COrderSetParadropTargetCommand     +0x30 line id (counts up per army), +0x34 the target province (land; mostly
                                     cities). Provinces checked against the map of the meme_ger recording.
  CIncreaseGameSpeedCommand / CDecreaseGameSpeedCommand: no fields (pausing is not a command)

Memory:
  COrdersGroup (handle 53): +0x1a8 cohesion; lines are COrderInstances in vectors at +0x98 (front lines and naval
    invasions), +0xb0 and +0xc8 (once held two lines with an invasion's provinces, ids one apart; other times
    pointers to things that aren't COrderInstances, so elements whose vtable isn't in the game's image are skipped)
  COrderInstance: +0x244 id, +0x248 byte executing, +0x70 provinces (int32: a front line's own provinces in order,
    an offensive line's path from the front to the target), +0x88 (ours, theirs) border pairs as CProvince pointers
    (front lines), +0x1f8 child lines (a front line's offensive lines), +0xb8 int32 a naval invasion's source
    province (0 for other lines; its +0x70 are then the target provinces). +0x180 is the front's name as an inline
    string, not numbers.
  Where a front line's start / end (0..100000) are kept is not found yet: the border pairs (+0x88) are the part it
    covers.
"""
import re

from hoi4ai import shared
from hoi4bridge import send

# bridge.cmdrec list CLASS PTR COUNT STRIDE: a vector's data pointer at PTR, its element count at COUNT
RULES = [
    "list COrderGroupCommand 0x30 0x38 8",
    "list COrderNewRootCommand 0x30 0x38 8",
    "list COrderSetPathCommand 0x38 0x40 4",
    "list COrderNewFallbackCommand 0x48 0x50 4",
    "list COrderSetInvasionSourceCommand 0x40 0x48 8",
    "list COrderSetParadropSourceCommand 0x38 0x40 8",     # a guess from the layout: check the first recording
]

COHESION = {0: "flexible", 1: "balanced", 2: "rigid", 3: "immobile"}
# COrdersGroup vectors of COrderInstance -> the kind of line found there
GROUP_LISTS = {0x98: "front", 0xb0: "list_b0", 0xc8: "list_c8"}


def install_rules():
    """Add RULES to bridge.cmdrec (again: the bridge keeps rules until the game closes, so rules sent before are
    replaced, not doubled)."""
    for r in RULES:
        send("bridge.cmdrec rules " + r.split()[1] + " off")
    for r in RULES:
        send("bridge.cmdrec " + r)
    # the bridge's own rule for COrderNewFrontCommand went with "rules off" only if we named it; it's built in


def _i32(b, off):
    return int.from_bytes(b[off:off + 4], "little", signed=True)


def _i64(b, off):
    return int.from_bytes(b[off:off + 8], "little", signed=True)


def _lists(extra):
    """{offset: [elements]} from the record's last column (L+0x30=[51:3,51:9] -> {0x30: ["51:3", "51:9"]})."""
    out = {}
    for m in re.finditer(r"L\+0x([0-9a-f]+)=\[([^\]]*)\]", extra or ""):
        out[int(m.group(1), 16)] = [x for x in m.group(2).split(",") if x]
    return out


def _divs(items):
    return [int(x.split(":")[1]) for x in items if ":" in x]


def _ints(items):
    return [int(x) for x in items]


def decode(cls, hexdump, extra=""):
    """What a battle-plan command says, or None for other classes. HEXDUMP: the record's first 0xa0 bytes."""
    b = bytes.fromhex(hexdump[:0x140])
    L = _lists(extra)
    army = _i32(b, 0x2c) if len(b) >= 0x30 and _i32(b, 0x28) == 53 else None
    if cls == "COrderGroupCommand":
        return {"what": "create_army", "theatre": _i32(b, 0x4c), "divisions": _divs(L.get(0x30, []))}
    if army is None:
        return None
    d = {"army": army}
    if cls == "COrderNewRootCommand":
        d.update(what="front_line", cfront=_i32(b, 0x4c), s=_i32(b, 0x50), start=_i64(b, 0x58), end=_i64(b, 0x60),
                 divisions=_divs(L.get(0x30, [])))
    elif cls == "COrderNewFrontCommand":
        d.update(what="offensive_line", parent=_i32(b, 0x68), points=_ints(L.get(0x50, [])),
                 divisions=_divs(L.get(0x38, [])))
    elif cls == "COrderEditRootCommand":
        d.update(what="edit_front_line", line=_i32(b, 0x30), bound_38=_i64(b, 0x38), bound_40=_i64(b, 0x40))
    elif cls == "COrderSetPathCommand":
        d.update(what="set_path", line=_i32(b, 0x30), points=_ints(L.get(0x38, [])))
    elif cls == "COrderNewFallbackCommand":
        d.update(what="fortification_line", provinces=_ints(L.get(0x48, [])))
    elif cls == "COrderExecuteCommand":
        d.update(what="execute" if _i32(b, 0x34) else "stop")
    elif cls == "COrderDeleteCommand":
        d.update(what="delete_line", line=_i32(b, 0x30))
    elif cls == "COrderDeleteAllCommand":
        d.update(what="delete_all")
    elif cls == "CSetOrderGroupCohesionTypeCommand":
        c = _i32(b, 0x30)
        d.update(what="cohesion", cohesion=COHESION.get(c, c))
    elif cls == "COrderSetInvasionSourceCommand":
        d.update(what="invasion_source", province=_i32(b, 0x38), divisions=_divs(L.get(0x40, [])))
    elif cls == "COrderSetParadropSourceCommand":
        d.update(what="paradrop_source", province=_i32(b, 0x34), divisions=_divs(L.get(0x38, [])))
    elif cls == "COrderSetParadropTargetCommand":
        d.update(what="paradrop_target", line=_i32(b, 0x30), province=_i32(b, 0x34))
    elif cls == "CAddNavalInvasionTargetCommand":
        d.update(what="invasion_target", line=_i32(b, 0x30), province=_i32(b, 0x34))
    elif cls == "CRemoveNavalInvasionTargetCommand":
        d.update(what="remove_invasion_target", line=_i32(b, 0x30), province=_i32(b, 0x34))
    else:
        return None
    return d


def _addresses(type_, ids):
    out = {}
    ids = list(ids)
    for k in range(0, len(ids), 100):
        part = ids[k:k + 100]
        for oid, r in zip(part, send(*[f"bridge.object {type_} {i}" for i in part])):
            m = re.search(r"OK (\w+).*@0x([0-9a-fA-F]+)", r)
            if m:
                out[oid] = (m.group(1), int(m.group(2), 16))
    return out


def snapshot(proc, army_ids):
    """Every line of these armies (COrdersGroup ids; other order ids are skipped):
    [(army, cohesion, kind, line id, parent line id or 0, executing, provinces, border pairs, source)]. Kind
    "invasion": a naval invasion, SOURCE its port province (else 0), provinces its targets. An army with no
    line gives one row with kind "-" so its cohesion is still recorded."""
    def rd(addr, n):
        raw = proc.read(addr, n)
        return int.from_bytes(raw, "little") if raw else 0

    def vec(addr, stride):
        data, n = rd(addr, 8), rd(addr + 8, 4)
        if not data or not 0 < n < 5000:
            return []
        raw = proc.read(data, n * stride) or b""
        return [int.from_bytes(raw[i:i + stride], "little") for i in range(0, len(raw), stride)]

    rows = []

    def line(aid, coh, kind, o, parent, depth=0):
        if not proc.in_image(rd(o, 8)):
            return
        lid = rd(o + 0x244, 4)
        provs = [p if p < 2 ** 31 else p - 2 ** 32 for p in vec(o + 0x70, 4)]
        pairs, source = [], rd(o + 0xb8, 4)
        if source:
            kind = "invasion"
        elif kind == "front" and not parent:
            ps = [rd(p + 0xa4, 4) for p in vec(o + 0x88, 8)]
            pairs = list(zip(ps[0::2], ps[1::2]))
        rows.append((aid, coh, kind if not parent else "offensive", lid, parent, rd(o + 0x248, 1), provs, pairs,
                     source))
        if depth < 3:
            for c in vec(o + 0x1f8, 8):
                line(aid, coh, kind, c, lid, depth + 1)

    for aid, (cls, a) in sorted(_addresses(53, army_ids).items()):
        if cls != "COrdersGroup":
            continue
        try:
            coh = rd(a + 0x1a8, 4)
            n0 = len(rows)
            for off, kind in GROUP_LISTS.items():
                for o in vec(a + off, 8):
                    line(aid, coh, kind, o, 0)
            if len(rows) == n0:
                rows.append((aid, coh, "-", 0, 0, 0, [], [], 0))
        except (OSError, TypeError):
            continue
    return rows


_country_ids = {}      # tag -> country id (bridge.tag of the owners the scan found)


def armies_of(tag, gap=1000, proc=None):
    """Ids of the country's armies (COrdersGroup, owner's country id at +0x3c). With PROC from the memory scan
    (shared.ids): the game's thread only answers a bridge.object per army that looks like TAG's (to drop stale
    copies) and, once, a bridge.tag per owner id. Without PROC or a scan, by asking for order ids 1, 2, ... until
    GAP ids in a row name nothing (deleted orders leave holes): late in a big game thousands of requests on the
    game's thread (~30 s)."""
    try:
        scanned = shared.ids("COrdersGroup", 53) if proc is not None else {}
    except Exception:
        scanned = {}
    if not scanned:
        return _probe_armies(tag, gap)
    owner = {oid: {_read_i32(proc, a + 0x3c) for a in addrs} for oid, addrs in scanned.items()}
    if tag not in _country_ids:
        ids = sorted({c for cs in owner.values() for c in cs if c is not None and 0 <= c < 100000})
        for c, r in zip(ids, send(*[f"bridge.tag {c}" for c in ids]) if ids else []):
            if r.startswith("OK") and "=" in r:
                _country_ids.setdefault(r.split("=", 1)[1].strip(), c)
    cid = _country_ids.get(tag)
    cand = sorted(oid for oid, cs in owner.items() if cid in cs) if cid is not None else []
    return [oid for oid, r in zip(cand, send(*[f"bridge.object 53 {k}" for k in cand]) if cand else [])
            if r.startswith("OK COrdersGroup") and re.search(rf"\+0x3c={tag}\b", r)]


def _read_i32(proc, addr):
    try:
        return proc.i32(addr)
    except OSError:
        return None


def _probe_armies(tag, gap):
    out, miss, i = [], 0, 1
    while miss < gap:
        ids = list(range(i, i + 100))
        i += 100
        for oid, r in zip(ids, send(*[f"bridge.object 53 {k}" for k in ids])):
            if not r.startswith("OK "):
                miss += 1
                continue
            miss = 0
            if r.startswith("OK COrdersGroup") and re.search(rf"\+0x3c={tag}\b", r):
                out.append(oid)
    return out


HEADER = "date\tarmy\tcohesion\tkind\tline\tparent\texecuting\tprovinces\tborder\tsource"


class PlanLog:
    """plans.tsv: the country's lines (snapshot()), written whenever they differ from the last ones written.
    Call step() as often as you like (each in-game hour); the army list is looked up again each new day."""

    def __init__(self, path, tag):
        self.path, self.tag = path, tag
        new = not __import__("os").path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")
        self.proc, self.armies, self.day, self.last = None, [], None, None
        shared.want("COrdersGroup")

    def step(self, date):
        """DATE: "YYYY-MM-DD HH:00". Returns the rows written (0 if nothing changed)."""
        if self.proc is None:
            from hoi4ai.memory import Process
            self.proc = Process()
        if date[:10] != self.day:
            self.armies, self.day = armies_of(self.tag, proc=self.proc), date[:10]
        rows = snapshot(self.proc, self.armies)
        if rows == self.last:
            return 0
        self.last = rows
        lines = format_rows(date, rows) or [f"{date}\t-\t-\t-\t0\t0\t0\t\t\t"]
        self.f.write("\n".join(lines) + "\n")
        self.f.flush()
        return len(lines)

    def close(self):
        self.f.close()


def format_rows(date, rows):
    out = []
    for aid, coh, kind, lid, parent, ex, provs, pairs, source in rows:
        out.append(f"{date}\t{aid}\t{COHESION.get(coh, coh)}\t{kind}\t{lid}\t{parent}\t{ex}"
                   f"\t{','.join(map(str, provs))}\t{' '.join(f'{a}:{b}' for a, b in pairs)}"
                   f"\t{source or ''}")
    return out
