"""Division templates of every country, recorded whenever they change.

TemplateLog(path).step(date, lines) rescans every template (handle 52, ids from 1 up; about 1 s) at the first call,
when the new record LINES hold a template command, every FULL_DAYS in-game days (effects such as a focus can change
templates without a command) and when a new id turns up past the highest known (probed each new day, about 0.1 s),
and writes what differs from the last scan:
  date  change (start / new / changed / removed)  id  owner  name  battalions  support
battalions are the designer's grid: columns separated by "|", top to bottom, empty slots left out
("infantry,infantry,infantry|infantry,infantry,infantry|..."); support is the support companies in order.

The commands themselves (in command_record.tsv) say who acted; edits keep the template id:
  CCreateDivisionTemplateCommand       a new template (a whole copy of it embedded at +0x28, owner country at +0x270)
  CUpdateDivisionTemplateCommand       the player's edit in the designer (same layout; the AI doesn't edit)
  CRemoveDivisionTemplateCommand       +0x28 template handle
  CSetObsoleteDivisionTemplateCommand  +0x28 template handle
A CReferencedDivisionTemplate (bridge.object 52 ID) has its name at +0x20, its battalion slots at +0x68 (pointer
array, count +0x74; slot = column * 5 + row; an empty slot points at something that isn't a CSubUnitDefinition),
support slots at +0x88, owner country id at +0x1ec.
"""
import os
import re
import struct

from hoi4bridge import send

CLASSES = ("CCreateDivisionTemplateCommand", "CUpdateDivisionTemplateCommand", "CRemoveDivisionTemplateCommand",
           "CSetObsoleteDivisionTemplateCommand")
HEADER = "date\tchange\tid\towner\tname\tbattalions\tsupport"
ROWS = 5
FULL_DAYS = 7


def _probe(ids):
    """{template id: address} for those of IDS that exist."""
    out = {}
    for k, r in zip(ids, send(*[f"bridge.object 52 {j}" for j in ids])):
        m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK") else None
        if m:
            out[k] = int(m.group(1), 16)
    return out


def _ids_and_addresses(max_gap=100):
    """{template id: address} for every template, scanning ids from 1 until MAX_GAP ids in a row are missing."""
    out, i = {}, 1
    while i - max(out, default=0) <= max_gap:
        out.update(_probe(range(i, i + 100)))
        i += 100
    return out


class TemplateLog:
    def __init__(self, path, names=None):
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        from hoi4ai.templates import Reader
        self.proc = Process()
        self.reader = Reader(self.proc)
        self.names = names or Names()
        self.last, self.day, self.days = None, None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def _slots(self, a, columns):
        p = self.proc
        data, n = p.u64(a), p.i32(a + 12) or 0
        if not data or not 0 < n <= 64:
            return ""
        out = []
        for e in struct.unpack_from(f"<{n}Q", p.read(data, 8 * n) or b"\0" * 8 * n):
            out.append(self.reader.name_at(e, 0x18) if e and p.class_of(e) == "CSubUnitDefinition" else "")
        if not columns:
            return ",".join(s for s in out if s)
        cols = [",".join(s for s in out[c:c + ROWS] if s) for c in range(0, len(out), ROWS)]
        while cols and not cols[-1]:
            cols.pop()
        return "|".join(cols)

    def scan(self):
        """{id: (owner tag, name, battalions, support)}."""
        from hoi4ai.templates import _string
        out = {}
        for tid, a in _ids_and_addresses().items():
            out[tid] = (self.names.tag(self.proc.i32(a + 0x1ec)), _string(self.proc, a + 0x20),
                        self._slots(a + 0x68, True), self._slots(a + 0x88, False))
        return out

    def step(self, date, lines=()):
        """DATE: the date for the rows, "12:00, 1 January, 1936" (the day is the text after the comma) or
        "1936-01-01 12:00" (the text before the space). LINES: the record lines added since the last call. Returns the
        rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        full = self.last is None or any(f"\t{c}\t" in ln for ln in lines for c in CLASSES)
        if day != self.day:
            self.day, self.days = day, self.days + 1
            if self.days % FULL_DAYS == 0:
                full = True
            elif not full:
                top = max(self.last, default=0)
                full = bool(_probe(range(top + 1, top + 51)))
        if not full:
            return 0
        now = self.scan()
        rows = []
        if self.last is None:
            rows = [("start", t, v) for t, v in sorted(now.items())]
        else:
            for t, v in sorted(now.items()):
                if t not in self.last:
                    rows.append(("new", t, v))
                elif self.last[t] != v:
                    rows.append(("changed", t, v))
            rows += [("removed", t, self.last[t]) for t in sorted(self.last) if t not in now]
        self.last = now
        for change, t, v in rows:
            self.f.write("\t".join((date, change, str(t)) + v) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
