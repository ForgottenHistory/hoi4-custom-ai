"""A country's state each in-game day, for recordings: what the player decides with, beside what they decide.

CountryLog(folder, tag).step(day) writes, once per day:
  country/DAY.txt.gz  the country's whole save-format block (bridge.country: production lines, stockpile
                      (production.equipments), research done, politics, focus, deployment queue, divisions, intel ...)
  country.tsv         a row of headline numbers: VARIABLES (script game variables through bridge.query), then stability
                      and war support from the block
  equipment.tsv       equipment variants the block names (handle 70): name, type, modules; written once each
  states.tsv          owner, controller and cores (tags, space-separated) of every state of every country: date, state,
                      owner, controller, cores. The first day lists every state, after that a row only where one changed
  control.tsv         who controls each province (live.Provinces): date, province, controller; changes only after day one
  war_stats.tsv       casualties and war-score parts of each war the country is in (tools/war_stats.py backfills): WAR_COLS
  battles.tsv         every land combat in the world each day: province, then per side (a and b: NOT attacker and defender, Switzerland is a at one front and b at another) the
                      divisions' owner tags, count, mean organisation and strength in percent (_battles)
  battle_divisions.tsv  the same combats per division: date, province, side (a/b), division id (the unit_snapshots id),
                      owner, organisation and strength (100000 = full). The fall in strength between days is the damage
  intel.tsv           the country's intel over every other existing country, 0-100 (negative: their counter-intelligence
                      outweighs it): civilian, army, navy, air. This is the player's fog of war: the map shows what army
                      intel allows (below 10% division counts are "?", then counts with an error range shrinking to
                      70%, template info from 30%, exact templates from 80%: the NIntel ARMY_* defines)

The block refers to templates and equipment by handle ({ id=96 type=70 }); equipment.tsv names the equipment ids,
division_templates.tsv (hoi4ai/templaterec.py, every country's templates) the template ids.
"""
import gzip
import os
import re
import struct

from hoi4bridge import country_block, query, send

VARIABLES = ["political_power", "command_power", "manpower", "num_of_factories", "num_of_civilian_factories",
             "num_of_military_factories", "num_of_naval_factories", "num_of_available_civilian_factories",
             "num_of_available_military_factories", "num_divisions", "fuel_k", "max_fuel_k", "army_experience",
             "navy_experience", "air_experience", "num_of_controlled_states"]
BLOCK_NUMBERS = ["stability", "war_support"]   # top-level lines of the block (the game variables read 0 for these)
BLOCK_TEXT = ["ruling_party"]                  # the government (its ideology group), from politics={ } of the block
INTEL = ["civilian", "army", "navy", "air"]


def numbers(tag, block):
    """{name: value text} for VARIABLES and BLOCK_NUMBERS."""
    out = dict.fromkeys(VARIABLES + BLOCK_NUMBERS + BLOCK_TEXT, "")
    lines = query(f'{tag} = {{ log = "' + "|".join(f"{v}=[?{v}]" for v in VARIABLES) + '" }')
    for part in (lines[0] if lines else "").strip().split("|"):
        k, _, v = part.partition("=")
        if k in out:
            out[k] = v
    for k in BLOCK_NUMBERS:
        m = re.search(rf"^\t\t{k}=(\S+)", block, re.M)
        out[k] = m.group(1) if m else ""
    m = re.search(r"^\t\t\truling_party=(\w+)", block, re.M)
    out["ruling_party"] = m.group(1) if m else ""
    return out


def states():
    """{state id: (owner tag, controller tag, cores)} for every state of every existing country (one script query);
    cores is the tags that have a core on the state, sorted, joined by spaces."""
    lines = query('every_country = { limit = { exists = yes } every_owned_state = { log = "O|[THIS.GetID]|[PREV.GetTag]" } '
                  'every_controlled_state = { limit = { NOT = { is_owned_by = PREV } } log = "C|[THIS.GetID]|[PREV.GetTag]" } '
                  'every_core_state = { log = "K|[THIS.GetID]|[PREV.GetTag]" } }')
    owner, ctrl, core = {}, {}, {}
    for line in lines:
        p = line.strip().split("|")
        if len(p) == 3 and p[1].isdigit() and p[0] in ("O", "C", "K"):
            if p[0] == "K":
                core.setdefault(int(p[1]), []).append(p[2])
            else:
                (owner if p[0] == "O" else ctrl)[int(p[1])] = p[2]
    return {i: (o, ctrl.get(i, o), " ".join(sorted(core.get(i, [])))) for i, o in owner.items()}


WAR_COLS = ["first", "second", "start", "first_casualties", "second_casualties"] + [
    f"{side}_{k}" for side in ("first", "second")
    for k in ("equipment_damage", "province_capture", "air_damage_str", "strategic_air", "sunk_ship", "convoy_attack",
              "score_casualties", "captured")]


def war_rows(block):
    """[[value text per WAR_COLS]] for every war_relation of the country's block: the war summary's casualties
    (manpower, cumulative, both sides) and each side's war-score parts (equipment damage, provinces, air, ships sunk,
    convoys, casualties; captured = number of provinces taken)."""
    from hoi4ai.wars import _blocks, _field
    rows = []
    for w in _blocks(block):
        row = [_field(w, "first") or "", _field(w, "second") or "", _field(w, "start_date") or "",
               _field(w, "first_casualties") or "0", _field(w, "second_casualties") or "0"]
        for side in ("first_vs_second", "second_vs_first"):
            m = re.search(r"war_score_" + side + r"=\{", w)
            part = w[m.end():w.find("\n\t\t\t\t\t\t}", m.end())] if m else ""
            for k in ("equipment_damage", "province_capture", "air_damage_str", "strategic_air", "sunk_ship", "convoy_attack"):
                v = re.search(r"\b" + k + r"=(-?[\d.]+)", part)
                row.append(v.group(1) if v else "0")
            v = re.search(r"\bcasualties=(-?[\d.]+)", part)
            row.append(v.group(1) if v else "0")
            c = re.search(r"captured_provinces=\{([^}]*)\}", part)
            row.append(str(len(c.group(1).split())) if c else "0")
        rows.append(row)
    return rows


def intel(tag):
    """[(target tag, civilian, army, navy, air)] for every other existing country (value texts)."""
    lines = query(f'every_country = {{ limit = {{ exists = yes NOT = {{ tag = {tag} }} }} {tag} = {{ log = "[PREV.GetTag]'
                  + "".join(f"|[?{k}_intel@PREV]" for k in INTEL) + '" } }')
    out = []
    for line in lines:
        p = line.strip().split("|")
        if len(p) == 1 + len(INTEL):
            out.append(tuple(p))
    return out


def handles(block, type_):
    """Ids of every { id=N type=TYPE } handle in the block."""
    return sorted({int(i) for i in re.findall(rf"id=(\d+) type={type_}\b", block)})


def _addresses(type_, ids):
    out = {}
    for k in range(0, len(ids), 100):
        part = ids[k:k + 100]
        for i, r in zip(part, send(*[f"bridge.object {type_} {i}" for i in part])):
            m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK ") else None
            if m:
                out[i] = int(m.group(1), 16)
    return out


class CountryLog:
    def __init__(self, folder, tag):
        from hoi4ai.memory import Process
        from hoi4ai.templates import Reader
        self.folder, self.tag, self.day = folder, tag, None
        os.makedirs(os.path.join(folder, "country"), exist_ok=True)
        self.proc = Process()
        self.reader = Reader(self.proc)
        self.f_num = self._open("country.tsv", "date\t" + "\t".join(VARIABLES + BLOCK_NUMBERS + BLOCK_TEXT))
        with open(os.path.join(folder, "country.tsv"), encoding="utf-8") as h:     # a file begun before a column existed keeps its columns
            self.cols = h.readline().rstrip("\n").split("\t")[1:]
        self.f_eq = self._open("equipment.tsv", "id\tname\ttype\tmodules")
        self.f_intel = self._open("intel.tsv", "date\ttarget\t" + "\t".join(INTEL))
        self.equipment = set()
        self.f_states = self._open("states.tsv", "date\tstate\towner\tcontroller\tcores")
        self.f_control = self._open("control.tsv", "date\tprovince\tcontroller")
        self.states, self.control = {}, {}      # last written, so a restart carries on instead of repeating everything
        with open(os.path.join(folder, "states.tsv"), encoding="utf-8") as h:
            for line in list(h)[1:]:
                p = line.rstrip("\n").split("\t")
                if len(p) == 5 and p[1].isdigit():
                    self.states[int(p[1])] = (p[2], p[3], p[4])
        with open(os.path.join(folder, "control.tsv"), encoding="utf-8") as h:
            for line in list(h)[1:]:
                p = line.rstrip("\n").split("\t")
                if len(p) == 3 and p[1].isdigit():
                    self.control[int(p[1])] = p[2]
        self.provinces, self.names = None, None
    def _open(self, name, header):
        path = os.path.join(self.folder, name)
        new = not os.path.exists(path)
        f = open(path, "a", encoding="utf-8")
        if new:
            f.write(header + "\n")
            f.flush()
        return f

    def step(self, day):
        """DAY: "YYYY-MM-DD". Records once per day; returns True when it did."""
        if day == self.day:
            return False
        self.day = day
        block = country_block(self.tag)
        with gzip.open(os.path.join(self.folder, "country", f"{day}.txt.gz"), "wt", encoding="utf-8") as g:
            g.write(block)
        n = numbers(self.tag, block)
        self.f_num.write(day + "\t" + "\t".join(n[k] for k in self.cols) + "\n")
        for row in intel(self.tag):
            self.f_intel.write(day + "\t" + "\t".join(row) + "\n")
        for sid, v in sorted(states().items()):
            if self.states.get(sid) != v:
                self.f_states.write(f"{day}\t{sid}\t{v[0]}\t{v[1]}\t{v[2]}\n")
                self.states[sid] = v
        self._control(day)
        new =[i for i in handles(block, 70) if i not in self.equipment]
        for eid, a in _addresses(70, new).items():
            v = self.reader.variant(a)
            self.f_eq.write(f"{eid}\t{v.name}\t{v.type}\t{','.join(v.modules)}\n")
            self.equipment.add(eid)
        for f in (self.f_num, self.f_eq, self.f_intel, self.f_states, self.f_control):
            f.flush()
        return True

    def _control(self, day):
        """Who controls each province (live.Provinces): every province the first day, then only the changes. A state held
        in part (a war's front line) shows here and not in states.tsv, which names one controller per state."""
        from hoi4ai.eventrec import Names
        from hoi4ai.live import Provinces
        if self.provinces is None:
            self.provinces, self.names = Provinces(self.proc), Names()
        ctrl = self.provinces.refresh().controller
        for p in range(1, len(ctrl)):
            c = int(ctrl[p])
            if c and self.control.get(p) != self.names.tag(c):
                self.control[p] = self.names.tag(c)
                self.f_control.write(f"{day}\t{p}\t{self.control[p]}\n")

    def close(self):
        for f in (self.f_num, self.f_eq, self.f_intel, self.f_states, self.f_control):
            f.close()


class WarLog:
    """war_stats.tsv (see the module docstring), read on its own thread once per in-game day: the country block costs
    about a second, too slow for cmdrec's date loop but far lighter than the ~100 s log step, which at speed 4 left
    three rows for the whole Polish campaign."""

    def __init__(self, folder, tag):
        self.folder, self.tag, self.day = folder, tag, None
        path = os.path.join(folder, "war_stats.tsv")
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write("date\t" + "\t".join(WAR_COLS) + "\n")
            self.f.flush()
        self.wars = {}                          # (first, second, start) -> last written values, so a restart carries on
        with open(path, encoding="utf-8") as h:
            for line in list(h)[1:]:
                p = line.rstrip("\n").split("\t")
                if len(p) == 1 + len(WAR_COLS):
                    self.wars[tuple(p[1:4])] = p[1:]

    def step(self, day):
        if day == self.day:
            return False
        self.day = day
        for row in war_rows(country_block(self.tag)):
            if self.wars.get(tuple(row[:3])) != row:
                self.wars[tuple(row[:3])] = row
                self.f.write(day + "\t" + "\t".join(row) + "\n")
        self.f.flush()
        return True

    def close(self):
        self.f.close()


class CombatTrace:
    """battle_raw.tsv: the LAST raw state of every land combat before it ended. A CLandCombat holds running counters
    (casualty and air-damage totals the game shows in its battle log) and is freed when the fight ends, so a once-a-day
    read never sees the final values. This polls the combat list about every second on its own thread, keeps each
    combat's latest bytes, and writes one row when it disappears: province, combat ticks (+0x40), game clock (+0x130),
    then hex of the combat (0x200 bytes), both sides (0x400 each) and each air entry (0xa0). Raw on purpose: decode
    from the file (the counters are not identified yet)."""

    COLS = "wall\tprovince\tticks\tclock\tcombat\tside_a\tside_b\tair_a\tair_b"

    def __init__(self, folder, every=1.0):
        import threading
        from hoi4ai.memory import Process
        self.proc, self.every, self.live, self._mgr, self._stop = Process(), every, {}, None, threading.Event()
        path = os.path.join(folder, "battle_raw.tsv")
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(self.COLS + "\n")
            self.f.flush()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                self._poll()
            except Exception as e:
                print(f"\ncombat trace: {type(e).__name__}: {e}")

    def _poll(self):
        import struct
        u64, rd = self.proc.u64, self.proc.read
        if self._mgr is None or self.proc.class_of(self._mgr) != "CCombatManager":
            found = self.proc.find_objects("CCombatManager")
            self._mgr = found[0] if found else None
        if self._mgr is None:
            return
        b = rd(self._mgr + 0x10, 4)
        count = struct.unpack("<i", b)[0] if b else 0
        ptr = u64(self._mgr + 8)
        now = set()
        for k in range(count if 0 < count < 5000 else 0):
            c = u64(ptr + 8 * k)
            if not c or self.proc.class_of(c) != "CLandCombat":
                continue
            sides = [u64(c + 0x28), u64(c + 0x30)]
            prov = u64(c + 0x38)
            if not prov or not all(sides) or any(self.proc.class_of(s) != "CLandCombatant" for s in sides):
                continue
            now.add(c)
            cb, sa, sb = rd(c, 0x200), rd(sides[0], 0x400), rd(sides[1], 0x400)
            air = []
            for s in sides:
                vp, cn = u64(s + 0x140), struct.unpack("<i", rd(s + 0x148, 4) or bytes(4))[0]
                parts = []
                for j in range(cn if 0 < cn < 100 else 0):
                    el = u64(vp + 8 * j)
                    if el and self.proc.class_of(el) == "CAirInLandCombat":
                        parts.append((rd(el, 0xa0) or b"").hex())
                air.append(",".join(parts))
            if cb and sa and sb:
                pid = struct.unpack("<i", rd(prov + 0xa4, 4) or bytes(4))[0]
                self.live[c] = (pid, cb, sa, sb, air)
        for c in [c for c in self.live if c not in now]:
            self._write(self.live.pop(c))

    def _write(self, snap):
        import struct, time
        pid, cb, sa, sb, air = snap
        ticks, clock = struct.unpack_from("<i", cb, 0x40)[0], struct.unpack_from("<i", cb, 0x130)[0]
        self.f.write("\t".join([str(int(time.time())), str(pid), str(ticks), str(clock), cb.hex(), sa.hex(), sb.hex(),
                                air[0], air[1]]) + "\n")
        self.f.flush()

    def close(self):
        self._stop.set()
        self.t.join(5)
        self.f.close()


class BattleLog:
    """battles.tsv and battle_divisions.tsv (see the module docstring), once per in-game day. Light (the combats are
    one list in memory, about a millisecond), so cmdrec calls it from its date loop and not from the slow log step: a
    step takes ~100 s, which at speed 4 skips most days."""

    def __init__(self, folder):
        from hoi4ai.memory import Process
        self.folder, self.day, self.names, self._mgr = folder, None, None, None
        self.proc = Process()
        self.f_battles = self._open("battles.tsv", "date	province	a_tags	a_divs	a_org	a_strength	b_tags	b_divs	b_org	b_strength	controller")
        self.f_battle_divs = self._open("battle_divisions.tsv", "date	province	side	division	owner	org	strength")
        self.f_battle_air = self._open("battle_air.tsv", "date\tprovince\tside\twing\t" + "\t".join(f"f{o:x}" for o in range(0x10, 0x3c, 4)))

    _open = CountryLog._open

    def step(self, day):
        if day == self.day:
            return False
        self.day = day
        self._battles(day)
        self.f_battles.flush()
        self.f_battle_divs.flush()
        self.f_battle_air.flush()
        return True

    def close(self):
        self.f_battles.close()
        self.f_battle_divs.close()
        self.f_battle_air.close()

    def _battles(self, day):
        """Every land combat in the world right now, one row each (battles.tsv). Sides a and b are the combat's two
        CLandCombatants (+0x28, +0x30); which one attacks is not known (the CLandCombat has no damage or start-date field:
        +0x130 is the game clock; duration = days a province appears, damage = the fall in strength in battle_divisions.tsv). Divisions are CArmy objects (offsets are relative to the object + 0x10, see
        docs/game-layouts.md): owner +0x1c8, organisation +0x490 and strength +0x458 (100000 = 100%). Per side: tags
        of its divisions (space-separated, in order), their count, mean organisation and mean strength in percent."""
        import struct
        from hoi4ai.eventrec import Names
        if self.names is None:
            self.names = Names()
        u64 = self.proc.u64

        def i32(a):
            b = self.proc.read(a, 4)
            return struct.unpack("<i", b)[0] if b else 0

        seen = set()
        # the CCombatManager (one object, found once) keeps the live combats as a vector at +0x8, count at +0x10:
        # land, naval and a few other objects, so the class is checked. Scanning all of memory for CLandCombat took 10 s.
        if getattr(self, "_mgr", None) is None or self.proc.class_of(self._mgr) != "CCombatManager":
            found = self.proc.find_objects("CCombatManager")
            self._mgr = found[0] if found else None
        if self._mgr is None:
            return
        ptr, count = u64(self._mgr + 8), i32(self._mgr + 0x10)
        combats = [u64(ptr + 8 * k) for k in range(count if 0 < count < 5000 else 0)]
        for c in combats:
            if not c or self.proc.class_of(c) != "CLandCombat":
                continue
            prov = u64(c + 0x38)
            sides = [u64(c + 0x28), u64(c + 0x30)]
            if not prov or not all(sides) or any(self.proc.class_of(s) != "CLandCombatant" for s in sides):
                continue                        # a freed combat the memory scan still finds
            pid = i32(prov + 0xa4)
            row, lines, air = [day, str(pid)], [], []
            for side, s in zip("ab", sides):
                ptr, n = u64(s + 0x20), i32(s + 0x28)
                tags, org, st = [], 0, 0
                for k in range(n if 0 <= n < 200 else 0):
                    el = u64(ptr + 8 * k)
                    if not el or self.proc.class_of(el) != "CArmy":
                        continue                # a stale slot in the list (division id 0, org and strength 0)
                    a = el + 0x10
                    tags.append(self.names.tag(i32(a + 0x1c8)))
                    org += i32(a + 0x490)
                    st += i32(a + 0x458)
                    lines.append(f"{day}\t{pid}\t{side}\t{i32(a + 0xc)}\t{tags[-1]}\t{i32(a + 0x490)}\t{i32(a + 0x458)}\n")
                row += [" ".join(tags), str(len(tags)), "%.1f" % (org / 1000 / len(tags)) if tags else "0",
                        "%.1f" % (st / 1000 / len(tags)) if tags else "0"]
                # air wings in this side's combat: CLandCombatant +0x140 vector (count +0x148) of CAirInLandCombat, each
                # holding a wing handle (type 69, id at +0xc). The other fields are not decoded yet: kept raw (ints at +0x10..+0x3c)
                vp, cn = u64(s + 0x140), i32(s + 0x148)
                for k in range(cn if 0 < cn < 100 else 0):
                    el = u64(vp + 8 * k)
                    if el and self.proc.class_of(el) == "CAirInLandCombat":
                        raw = struct.unpack("<11i", self.proc.read(el + 0x10, 44) or bytes(44))
                        air.append(f"{day}\t{pid}\t{side}\t{i32(el + 0xc)}\t" + "\t".join(map(str, raw)) + "\n")
            if "".join(lines) in seen:
                continue                        # the scan can list one combat twice
            seen.add("".join(lines))
            self.f_battle_divs.write("".join(lines))
            self.f_battle_air.write("".join(air))
            row.append(self.names.tag(i32(prov + 0x188)))       # who holds the province today: the outcome, day by day
            self.f_battles.write("\t".join(row) + "\n")
