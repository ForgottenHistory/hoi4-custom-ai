"""A country's state each in-game day, for recordings: what the player decides with, beside what they decide.

CountryLog(folder, tag).step(day) writes, once per day:
  country/DAY.txt.gz  the country's whole save-format block (bridge.country: production lines, stockpile
                      (production.equipments), research done, politics, focus, deployment queue, divisions, intel ...)
  country.tsv         a row of headline numbers: VARIABLES (script game variables through bridge.query), then stability
                      and war support from the block
  equipment.tsv       equipment variants the block names (handle 70): name, type, modules; written once each
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
INTEL = ["civilian", "army", "navy", "air"]


def numbers(tag, block):
    """{name: value text} for VARIABLES and BLOCK_NUMBERS."""
    out = dict.fromkeys(VARIABLES + BLOCK_NUMBERS, "")
    lines = query(f'{tag} = {{ log = "' + "|".join(f"{v}=[?{v}]" for v in VARIABLES) + '" }')
    for part in (lines[0] if lines else "").strip().split("|"):
        k, _, v = part.partition("=")
        if k in out:
            out[k] = v
    for k in BLOCK_NUMBERS:
        m = re.search(rf"^\t\t{k}=(\S+)", block, re.M)
        out[k] = m.group(1) if m else ""
    return out


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
        self.f_num = self._open("country.tsv", "date\t" + "\t".join(VARIABLES + BLOCK_NUMBERS))
        self.f_eq = self._open("equipment.tsv", "id\tname\ttype\tmodules")
        self.f_intel = self._open("intel.tsv", "date\ttarget\t" + "\t".join(INTEL))
        self.equipment = set()

    def _open(self, name, header):
        path = os.path.join(self.folder, name)
        new = not os.path.exists(path)
        f = open(path, "a", encoding="utf-8")
        if new:
            f.write(header + "\n")
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
        self.f_num.write(day + "\t" + "\t".join(n[k] for k in VARIABLES + BLOCK_NUMBERS) + "\n")
        for row in intel(self.tag):
            self.f_intel.write(day + "\t" + "\t".join(row) + "\n")
        new = [i for i in handles(block, 70) if i not in self.equipment]
        for eid, a in _addresses(70, new).items():
            v = self.reader.variant(a)
            self.f_eq.write(f"{eid}\t{v.name}\t{v.type}\t{','.join(v.modules)}\n")
            self.equipment.add(eid)
        for f in (self.f_num, self.f_eq, self.f_intel):
            f.flush()
        return True

    def close(self):
        for f in (self.f_num, self.f_eq, self.f_intel):
            f.close()
