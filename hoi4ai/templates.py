"""A country's division templates, read live, and what each fights like: soft and hard attack, speed, combat width
and terrain modifiers, worked out from the game's files (through the active mods) as the game does.

Live (verified 1.19.4 hotfix, 2026-11-09):
- CArmy +0x3a8 -> CReferencedDivisionTemplate: name std::string +0x20, battalion grid {data, cap, count} at +0x68
  (5 per column; count up to the last used column), support companies at +0x88; each slot a CSubUnitDefinition*
  (its key, e.g. "mountaineers", at +0x18; +0x68 is its sprite, "infantry") or a shared
  TNullObject<CSubUnitDefinition> for an empty slot.
- CArmy +0x358: the equipment the division holds, {data, cap, count} of 16-byte entries (CEquipmentVariant*,
  amount x100000).
- CEquipmentVariant: name std::string +0x28, CEquipmentType* +0x3f0 (name +0x18), modules +0xb8 (16-byte
  entries: slot key, CEquipmentModule* (name +0x18)), upgrades +0x70 (16-byte entries: CEquipmentUpgrade*
  (name +0x18), level in the low byte).

Files: a battalion's equipment stats (archetype, then the type) plus its modules' add_stats and upgrades' add_stats
x level, times (1 + modules' multiply_stats). Each equipment slot of a battalion averages the variants the
division holds of that archetype, by amount. Battalion attack = equipment attack x (1 + battalion modifier +
researched tech modifiers for the battalion or its categories). Speed comes from the transport if the battalion
has one, else the slowest equipment, x (1 + the battalion's maximum_speed). Division: attacks and width summed,
speed the slowest line battalion (support companies keep up), terrain modifiers averaged over all battalions
(support companies included).
Not counted: doctrines, national spirits, generals, experience. Checked against the template designer
(GER 1939): Panzer-Division soft 77 / hard 115 both match; speed 7.4 vs 8.6 and infantry 4.0 vs 4.2 km/h (doctrine
speed bonuses).
"""
import collections
import re
import struct

from hoi4bridge import send

from . import pdx
from .gamefiles import GameFiles

TERRAIN_KEYS = ("forest", "hills", "mountain", "jungle", "marsh", "plains", "urban", "desert", "river",
                "amphibious", "fort")


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _stats(block):
    return {k: _num(v) for k, _, v in block if isinstance(v, str) and re.fullmatch(r"-?[\d.]+", v)}


class FileData:
    """Battalions, equipment, modules, upgrades and tech bonuses from the game's files."""

    def __init__(self, files=None):
        files = files or GameFiles()
        read = lambda p: pdx.parse(open(p, encoding="utf-8-sig", errors="replace").read())
        self.subunits = {}
        for _, path in sorted(files.listdir("common/units", ".txt").items()):
            for name, _, body in pdx.get(read(path), "sub_units", []) or []:
                if isinstance(body, list):
                    self.subunits[name] = body
        self.equipment, self.modules, self.upgrades = {}, {}, {}
        for sub, key, store in (("common/units/equipment", "equipments", self.equipment),
                                ("common/units/equipment/modules", "equipment_modules", self.modules),
                                ("common/units/equipment/upgrades", "upgrades", self.upgrades)):
            for _, path in sorted(files.listdir(sub, ".txt").items()):
                for name, _, body in pdx.get(read(path), key, []) or []:
                    if isinstance(body, list):
                        store[name] = body
        # techs that change battalion stats: {tech: [(battalion or category, {stat: value})]}
        self.tech_bonus = {}
        for _, path in sorted(files.listdir("common/technologies", ".txt").items()):
            for name, _, body in pdx.get(read(path), "technologies", []) or []:
                if not isinstance(body, list):
                    continue
                for k, _, v in body:
                    if isinstance(v, list) and (k in self.subunits or k.startswith("category_")):
                        s = {x: y for x, y in _stats(v).items()
                             if x in COMBAT + ("maximum_speed",)}
                        if s:
                            self.tech_bonus.setdefault(name, []).append((k, s))

    def archetype(self, eq):
        b = self.equipment.get(eq, [])
        return pdx.get(b, "archetype") or eq

    def equipment_stats(self, eq, modules=(), upgrades=()):
        b = self.equipment.get(eq, [])
        st = dict(_stats(self.equipment.get(self.archetype(eq), [])))
        st.update(_stats(b))
        mult = collections.Counter()
        # newer chassis raise what their modules add (light_tank_chassis_2: soft/hard attack +5%)
        boost = _stats(pdx.get(self.equipment.get(self.archetype(eq), []), "module_add_stats_multiplier", []) or [])
        boost.update(_stats(pdx.get(b, "module_add_stats_multiplier", []) or []))
        for m in modules:
            mb = self.modules.get(m, [])
            for k, v in _stats(pdx.get(mb, "add_stats", []) or []).items():
                st[k] = st.get(k, 0) + v * (1 + boost.get(k, 0))
            for k, v in _stats(pdx.get(mb, "multiply_stats", []) or []).items():
                mult[k] += v
        for u, level in upgrades:
            ub = self.upgrades.get(u, [])
            for k, v in _stats(pdx.get(ub, "add_stats", []) or []).items():
                st[k] = st.get(k, 0) + v * level
        return {k: v * (1 + mult[k]) for k, v in st.items()}

    def categories(self, unit):
        return pdx.values(pdx.get(self.subunits.get(unit, []), "categories", []) or [])


class Variant:
    __slots__ = ("name", "type", "modules", "upgrades")

    def __init__(self, name, type_, modules, upgrades):
        self.name, self.type, self.modules, self.upgrades = name, type_, modules, upgrades


def _string(proc, a):
    b = proc.read(a, 32)
    if not b:
        return ""
    n, cap = struct.unpack_from("<QQ", b, 16)
    if not 0 <= n < 512:
        return ""
    raw = b[:n] if cap < 16 else proc.read(struct.unpack_from("<Q", b, 0)[0], n)
    return (raw or b"").decode("utf-8", errors="replace")


def _vector(proc, a, stride, limit=64):
    data, n = proc.u64(a), proc.i32(a + 12) or 0
    if not data or not 0 < n <= limit:
        return []
    raw = proc.read(data, n * stride) or b""
    return [raw[i * stride:(i + 1) * stride] for i in range(len(raw) // stride)]


class Reader:
    """Live templates and equipment, with caches (designs and templates are shared by many divisions)."""

    def __init__(self, proc):
        self.proc = proc
        self._variants, self._templates, self._names = {}, {}, {}

    def name_at(self, ptr, off):
        if (ptr, off) not in self._names:
            self._names[ptr, off] = _string(self.proc, ptr + off)
        return self._names[ptr, off]

    def template(self, tp):
        if tp not in self._templates:
            p = self.proc
            slots = lambda a: [self.name_at(s, 0x18) for s in
                               (struct.unpack_from("<Q", e)[0] for e in _vector(p, a, 8, 25))
                               if s and p.class_of(s) == "CSubUnitDefinition"]
            self._templates[tp] = (_string(p, tp + 0x20), slots(tp + 0x68), slots(tp + 0x88))
        return self._templates[tp]

    def variant(self, v):
        if v not in self._variants:
            p = self.proc
            mods = [self.name_at(struct.unpack_from("<Q", e, 8)[0], 0x18) for e in _vector(p, v + 0xb8, 16)]
            ups = [(self.name_at(struct.unpack_from("<Q", e, 0)[0], 0x18), e[8]) for e in _vector(p, v + 0x70, 16)]
            self._variants[v] = Variant(_string(p, v + 0x28), self.name_at(p.u64(v + 0x3f0), 0x18),
                                        [m for m in mods if m], [(u, lv) for u, lv in ups if u])
        return self._variants[v]

    def division(self, addr):
        """(template name, battalions, support companies, [(Variant, amount)])."""
        p = self.proc
        name, bats, sup = self.template(p.u64(addr + 0x3a8))
        held = []
        for e in _vector(p, addr + 0x358, 16):
            v, amount = struct.unpack_from("<Qq", e, 0)
            if v:
                held.append((self.variant(v), amount / 100000))
        return name, bats, sup, held


COMBAT = ("soft_attack", "hard_attack", "defense", "breakthrough")  # summed over battalions

def division_stats(fd, bats, sup, held, techs):
    """Stats of a division from its battalions and support companies, the equipment it holds and the researched
    TECHS."""
    by_arch = collections.defaultdict(list)
    for v, amount in held:
        by_arch[fd.archetype(v.type)].append((v, amount))
    cache = {}

    def eq_stats(arch):
        if arch not in cache:
            vs = [(v, a) for v, a in by_arch.get(arch, []) if a > 0]
            if not vs:  # none held: the archetype's own numbers
                cache[arch] = fd.equipment_stats(arch)
            else:
                tot = sum(a for _, a in vs)
                acc = collections.Counter()
                for v, a in vs:
                    for k, x in fd.equipment_stats(v.type, v.modules, v.upgrades).items():
                        acc[k] += x * a / tot
                cache[arch] = dict(acc)
        return cache[arch]

    out = collections.Counter()
    speeds, terrain = [], collections.defaultdict(lambda: collections.Counter())
    hard, armor, pierce = [], [], []
    units = bats + sup
    for i, u in enumerate(units):
        b = fd.subunits.get(u, [])
        need = [k for k, _, _ in (pdx.get(b, "need", []) or [])]
        mod = _stats(b)
        cats = set(fd.categories(u)) | {u}
        bonus = collections.Counter()
        for t in techs:
            for key, s in fd.tech_bonus.get(t, ()):
                if key in cats:
                    bonus.update(s)
        for stat in COMBAT:
            out[stat] += sum(eq_stats(a).get(stat, 0) for a in need) * (1 + mod.get(stat, 0) + bonus[stat])
        transport = pdx.get(b, "transport")
        base = [eq_stats(transport).get("maximum_speed")] if transport else \
            [eq_stats(a).get("maximum_speed") for a in need]
        base = [s for s in base if s]
        if i < len(bats):  # support companies keep up with the division
            speeds.append((min(base) if base else 4.0) * (1 + mod.get("maximum_speed", 0) + bonus["maximum_speed"]))
        out["combat_width"] += mod.get("combat_width", 0)
        out["max_strength"] += mod.get("max_strength", 0)
        out["max_org"] += mod.get("max_organisation", 0)
        eqs = [eq_stats(a) for a in need]
        hard.append(max((e.get("hardness", 0) for e in eqs), default=0))
        armor.append(max((e.get("armor_value", 0) for e in eqs), default=0) * (1 + mod.get("armor_value", 0)))
        pierce.append(max((e.get("ap_attack", 0) for e in eqs), default=0) * (1 + mod.get("ap_attack", 0)))
        for k, _, v in b:
            if k in TERRAIN_KEYS and isinstance(v, list):
                for kk, x in _stats(v).items():
                    terrain[k][kk] += x
    out["maximum_speed"] = min(speeds) if speeds else 0
    n = max(1, len(units))
    # the game's maximum organisation: the average over battalions and support companies (doctrines and other
    # modifiers left out: GER 1939 infantry 48.3 here, 46.9 in the game)
    out["max_org"] /= n
    # roughly the game's: hardness averaged; armour and piercing weighted toward the best battalion
    out["hardness"] = sum(hard) / n
    out["armor"] = 0.3 * max(armor, default=0) + 0.7 * sum(armor) / n
    out["piercing"] = 0.4 * max(pierce, default=0) + 0.6 * sum(pierce) / n
    out["terrain"] = {t: {k: x / n for k, x in c.items()} for t, c in terrain.items()}
    return out


def terrain_widths(files=None):
    """{terrain: (combat width, extra width per extra direction attacked from)} from common/terrain."""
    files = files or GameFiles()
    out = {}
    for _, path in sorted(files.listdir("common/terrain", ".txt").items()):
        tree = pdx.parse(open(path, encoding="utf-8-sig", errors="replace").read())
        for name, _, body in pdx.get(tree, "categories", []) or []:
            if isinstance(body, list) and pdx.get(body, "combat_width") is not None:
                out[name] = (_num(pdx.get(body, "combat_width")), _num(pdx.get(body, "combat_support_width")))
    return out


def researched(tag, fd):
    """The techs in fd.tech_bonus the country has (asked of the game, one batch)."""
    names = sorted(fd.tech_bonus)
    replies = send(*[f"eval_trigger {tag} = {{ has_tech = {t} }}" for t in names]) if names else []
    return {t for t, r in zip(names, replies) if r.rstrip().endswith("yes")}


def _address(did):
    m = None
    for t in (51, 4713):  # a division made later in a loaded game can be type 4713 (see divisions.Snapshotter)
        r = send(f"bridge.object {t} {did}")[0]
        m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK CArmy") else None
        if m:
            break
    return int(m.group(1), 16) if m else 0


def country(tag, divisions, proc, files=None):
    """{template name: {"count", "battalions", "support", "equipment": {variant: amount}, stats..., "min"/"max"
    of soft attack and speed over its divisions}} for TAG's DIVISIONS (hoi4ai.divisions.Division)."""
    fd = FileData(files)
    techs = researched(tag, fd)
    rd = Reader(proc)
    out = {}
    for d in divisions:
        if d.owner != tag:
            continue
        a = _address(d.id)
        if not a:
            continue
        name, bats, sup, held = rd.division(a)
        s = division_stats(fd, bats, sup, held, techs)
        t = out.setdefault(name, {"count": 0, "battalions": collections.Counter(bats),
                                  "support": collections.Counter(sup), "equipment": collections.Counter(),
                                  "rows": []})
        t["count"] += 1
        for v, amount in held:
            t["equipment"][v.name or v.type] += amount
        t["rows"].append(s)
    for t in out.values():
        rows = t.pop("rows")
        for k in COMBAT + ("maximum_speed", "combat_width", "max_strength", "max_org", "hardness", "armor",
                  "piercing"):
            vals = [r[k] for r in rows]
            t[k] = sum(vals) / len(vals)
            t[k + "_range"] = (min(vals), max(vals))
        t["terrain"] = rows[0]["terrain"]
    return out
