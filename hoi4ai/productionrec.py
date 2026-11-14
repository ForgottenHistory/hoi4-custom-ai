"""Military production lines of every country, recorded whenever they change.

ProductionLog(path).step(date, lines) reads every line (handle 56; about 160 in a 1936 game, under a second) at the
first call, each new in-game day and whenever the record LINES hold a production command, and writes what differs:
  date  change (start / new / changed / removed)  line  owner  design_id  design  factories  priority  manufacturer
owner is the design's owner (a line making a licensed design shows the licensor); priority 0 is the top of the
list; manufacturer is the military industrial organisation's id (handle 79), manufacturer_name its token
(GER_messerschmitt_organization), from the industrial_organisations of the owner's save-format block
(bridge.country, read once per country when one of its MIO ids is first seen). The AI changes its lines without
commands, so the daily read is what catches them.

The player's commands (in command_record.tsv), with the line as handle { 56, id }:
  CAddProductionLineCommand              +0x28 country id, +0x2c { 70, design }, +0x34 { 79, manufacturer }
  CAddProductionLineFactoriesCommand     +0x28 line, +0x30 factories added (negative to take away)
  CSetProductionLineCommand              +0x28 line, +0x30 { 70, new design } (switching the line's design)
  CSetIndustrialManufacturerCommand      +0x28 country id, +0x2c line, +0x34 { 79, manufacturer } (assign, switch;
                                         removing the MIO sends manufacturer 0, the line's value without one)
  CChangeProductionLinePriorityCommand   +0x28 line, +0x30 new priority
  CRemoveProductionLineCommand           +0x28 line
A CMilitaryProductionLine (bridge.object 56 ID) has its id at +0xc, requested factories at +0x18, priority at +0x44,
the design (CEquipmentVariant pointer; its id at +0xc, owner country at +0x1c) at +0x88, manufacturer id at +0x94.
Naval lines (dockyards) aren't covered yet.
"""
import os
import re

from hoi4bridge import send

CLASSES = ("CAddProductionLineCommand", "CAddProductionLineFactoriesCommand", "CSetProductionLineCommand",
           "CSetIndustrialManufacturerCommand", "CChangeProductionLinePriorityCommand", "CRemoveProductionLineCommand")
HEADER = "date\tchange\tline\towner\tdesign_id\tdesign\tfactories\tpriority\tmanufacturer\tmanufacturer_name"
_MIO = re.compile(r"^\s*(\w+)=\{\s*$\s*id=\{ id=(\d+) type=79 \}", re.M)


def _addresses(max_gap=100):
    """{line id: address} for every military production line, scanning ids from 1 until MAX_GAP in a row miss."""
    out, i = {}, 1
    while i - max(out, default=0) <= max_gap:
        ids = range(i, i + 100)
        for k, r in zip(ids, send(*[f"bridge.object 56 {j}" for j in ids])):
            m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK") and "ProductionLine" in r else None
            if m:
                out[k] = int(m.group(1), 16)
        i += 100
    return out


class ProductionLog:
    def __init__(self, path, names=None):
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        from hoi4ai.templates import Reader
        self.proc = Process()
        self.reader = Reader(self.proc)
        self.names = names or Names()
        self.last, self.day = None, None
        self.mio, self.mio_read = {}, set()   # MIO id -> token; tags whose block was read
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def mio_name(self, mid, tag):
        """Token of MIO MID, reading TAG's block for its MIOs if MID isn't known yet."""
        if mid not in self.mio and tag and tag not in self.mio_read:
            self.mio_read.add(tag)
            text = "\n".join(send(f"bridge.country {tag}"))
            self.mio.update((int(i), t) for t, i in _MIO.findall(text))
        return self.mio.get(mid, "")

    def scan(self):
        """{line id: (owner, design id, design name, factories, priority, manufacturer, manufacturer name)}."""
        p, out = self.proc, {}
        for lid, a in _addresses().items():
            v = p.u64(a + 0x88)
            if v and p.class_of(v) == "CEquipmentVariant":
                owner, did, name = self.names.tag(p.i32(v + 0x1c)), str(p.i32(v + 0xc)), self.reader.variant(v).name
            else:
                owner = did = name = ""
            mid = p.i32(a + 0x94)
            out[lid] = (owner, did, name, str(p.i32(a + 0x18)), str(p.i32(a + 0x44)), str(mid),
                        self.mio_name(mid, owner) if mid > 0 else "")
        return out

    def step(self, date, lines=()):
        """DATE: the date for the rows, "12:00, 1 January, 1936" (the day is the text after the comma) or
        "1936-01-01 12:00" (the text before the space). LINES: the record lines added since the last call. Returns
        the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        if self.last is not None and day == self.day and not any(f"\t{c}\t" in ln for ln in lines for c in CLASSES):
            return 0
        self.day = day
        now = self.scan()
        if self.last is None:
            rows = [("start", k, v) for k, v in sorted(now.items())]
        else:
            rows = [("new" if k not in self.last else "changed", k, v) for k, v in sorted(now.items())
                    if self.last.get(k) != v]
            rows += [("removed", k, self.last[k]) for k in sorted(self.last) if k not in now]
        self.last = now
        for change, k, v in rows:
            self.f.write("\t".join((date, change, str(k)) + v) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
