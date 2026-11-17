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
        self.f_wars = self._open("war_stats.tsv", "date\t" + "\t".join(WAR_COLS))
        self.f_battles = self._open("battles.tsv", "date\tprovince\ta_tags\ta_divs\ta_org\ta_strength\tb_tags\tb_divs\tb_org\tb_strength")
        self.f_battle_divs = self._open("battle_divisions.tsv", "date	province	side	division	owner	org	strength")
        self.wars = {}                          # (first, second, start) -> last written values
        with open(os.path.join(folder, "war_stats.tsv"), encoding="utf-8") as h:
            for line in list(h)[1:]:
                p = line.rstrip("\n").split("\t")
                if len(p) == 1 + len(WAR_COLS):
                    self.wars[tuple(p[1:4])] = p[1:]

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
        for row in war_rows(block):
            if self.wars.get(tuple(row[:3])) != row:
                self.wars[tuple(row[:3])] = row
                self.f_wars.write(day + "\t" + "\t".join(row) + "\n")
        self._battles(day)
        new = [i for i in handles(block, 70) if i not in self.equipment]
        for eid, a in _addresses(70, new).items():
            v = self.reader.variant(a)
            self.f_eq.write(f"{eid}\t{v.name}\t{v.type}\t{','.join(v.modules)}\n")
            self.equipment.add(eid)
        for f in (self.f_num, self.f_eq, self.f_intel, self.f_states, self.f_control, self.f_wars, self.f_battles, self.f_battle_divs):
            f.flush()
        return True

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
        for c in self.proc.find_objects("CLandCombat"):
            prov = u64(c + 0x38)
            sides = [u64(c + 0x28), u64(c + 0x30)]
            if not prov or not all(sides) or any(self.proc.class_of(s) != "CLandCombatant" for s in sides):
                continue                        # a freed combat the memory scan still finds
            pid = i32(prov + 0xa4)
            row, lines = [day, str(pid)], []
            for side, s in zip("ab", sides):
                ptr, n = u64(s + 0x20), i32(s + 0x28)
                tags, org, st = [], 0, 0
                for k in range(n if 0 <= n < 200 else 0):
                    a = u64(ptr + 8 * k) + 0x10
                    tags.append(self.names.tag(i32(a + 0x1c8)))
                    org += i32(a + 0x490)
                    st += i32(a + 0x458)
                    lines.append(f"{day}\t{pid}\t{side}\t{i32(a + 0xc)}\t{tags[-1]}\t{i32(a + 0x490)}\t{i32(a + 0x458)}\n")
                row += [" ".join(tags), str(len(tags)), "%.1f" % (org / 1000 / len(tags)) if tags else "0",
                        "%.1f" % (st / 1000 / len(tags)) if tags else "0"]
            if "".join(lines) in seen:
                continue                        # the scan can list one combat twice
            seen.add("".join(lines))
            self.f_battle_divs.write("".join(lines))
            self.f_battles.write("\t".join(row) + "\n")

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
        for f in (self.f_num, self.f_eq, self.f_intel, self.f_states, self.f_control, self.f_wars, self.f_battles, self.f_battle_divs):
            f.close()
