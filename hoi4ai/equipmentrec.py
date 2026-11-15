"""Equipment designs of every country, recorded as they are made and made obsolete (production lines: productionrec).

DesignLog(path).step(date, lines) writes to equipment_designs.tsv:
  date  change  id  owner  name  type  modules  based_on
change: start (every design at the first call), new (a design made since), obsolete / unobsolete (the country's
obsolete flag, from the command).
A design is never edited: the designer saves a new one (an "upgrade" keeps the old name: Bf 109 D with cannons is a
new id, based on the old Bf 109 D, which the player then usually makes obsolete). Existing equipment isn't converted.

New designs get the next free id (handle 70, shared by all countries), so each new in-game day and whenever the
record LINES hold a design command the ids past the highest known are probed. The first call takes the designs
from the memory scan (shared.ids; the bridge only for an id with more than one address) and probes past the
highest; without a scan it probes every id. The commands:
  CCreateEquipmentVariantCommand       +0x30 country id, +0x34 handle type 70, +0x38 the design it's based on,
                                       +0x40 the new name (std::string); the new id isn't in it
  CSetObsoleteEquipmentVariantCommand  +0x28 handle type 70, +0x2c design id, +0x30 flag (byte, 1 = obsolete)
A CEquipmentVariant (bridge.object 70 ID) has its owner country id at +0x1c, name at +0x28, type and modules as
templates.Reader.variant reads them. The obsolete flag isn't on the variant (the country holds it), so a design made
obsolete before recording started shows as not obsolete.
"""
import os
import re
import struct

from hoi4ai import shared
from hoi4bridge import send

VARIANT = "CEquipmentVariant"
CREATE, OBSOLETE = "CCreateEquipmentVariantCommand", "CSetObsoleteEquipmentVariantCommand"
HEADER = "date\tchange\tid\towner\tname\ttype\tmodules\tbased_on"


def _probe(ids):
    """{design id: address} for those of IDS that exist."""
    out = {}
    if not ids:
        return out
    for k, r in zip(ids, send(*[f"bridge.object 70 {j}" for j in ids])):
        m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK") and "CEquipmentVariant" in r else None
        if m:
            out[k] = int(m.group(1), 16)
    return out


def _bytes(line, cls):
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] != cls:
        return None
    try:
        return bytes.fromhex(p[7])
    except ValueError:
        return None


def _i32(b, off):
    return struct.unpack_from("<i", b, off)[0]


class DesignLog:
    def __init__(self, path, names=None):
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        from hoi4ai.templates import Reader
        self.proc = Process()
        self.reader = Reader(self.proc)
        shared.want(VARIANT)
        self.names = names or Names()
        self.addr, self.top, self.day = None, 0, None
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def _row(self, date, change, did, based_on=""):
        a = self.addr.get(did) or _probe([did]).get(did)
        if a:
            v = self.reader.variant(a)
            owner, name, typ, mods = self.names.tag(self.proc.i32(a + 0x1c)), v.name, v.type, ",".join(v.modules)
        else:
            owner = name = typ = mods = ""
        self.f.write("\t".join((date, change, str(did), owner, name, typ, mods, str(based_on))) + "\n")

    def _all(self):
        try:
            scanned = shared.ids(VARIANT, 70)
        except Exception:
            scanned = {}
        out = {i: a[0] for i, a in scanned.items() if len(a) == 1}
        out.update(_probe(sorted(i for i, a in scanned.items() if len(a) > 1)))
        i = max(out) + 1 if out else 1
        while i - max(out, default=0) <= 100:
            out.update(_probe(range(i, i + 100)))
            i += 100
        return out

    def step(self, date, lines=()):
        """DATE: the date for the rows, "12:00, 1 January, 1936" or "1936-01-01 12:00". LINES: the record lines added
        since the last call. Returns the rows written."""
        n = 0
        if self.addr is None:
            self.addr = self._all()
            self.top = max(self.addr, default=0)
            for did in sorted(self.addr):
                self._row(date, "start", did)
            n += len(self.addr)
        creates = []   # (country id, name, based on) in record order
        for ln in lines:
            b = _bytes(ln, CREATE)
            if b and len(b) >= 0x60 and _i32(b, 0x34) == 70:
                ln_ = _i32(b, 0x50)
                name = b[0x40:0x40 + ln_].decode("utf-8", "replace") if 0 <= ln_ < 16 else ""
                creates.append((_i32(b, 0x30), name, _i32(b, 0x38)))
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        if creates or day != self.day:
            self.day = day
            found = _probe(range(self.top + 1, self.top + 51))
            while found:
                self.addr.update(found)
                for did in sorted(found):
                    a = found[did]
                    owner, name = self.proc.i32(a + 0x1c), self.reader.variant(a).name
                    base = next((c for c in creates if c[0] == owner and c[1] == name), None)
                    if base:
                        creates.remove(base)
                    self._row(date, "new", did, base[2] if base else "")
                    n += 1
                self.top = max(found)
                found = _probe(range(self.top + 1, self.top + 51))
        for ln in lines:
            b = _bytes(ln, OBSOLETE)
            if b and len(b) >= 0x31 and _i32(b, 0x28) == 70:
                self._row(date, "obsolete" if b[0x30] else "unobsolete", _i32(b, 0x2c))
                n += 1
        self.f.flush()
        return n

    def close(self):
        self.f.close()
