"""Every division in the game, from one bridge.snap (~15 ms in the game, read back from a file)."""
import os
import time

from hoi4bridge import send

# CArmy (handle 51) fields: owner, province, destination (0 = none), current organisation, strength, template name,
# main sub-unit type (the one its map counter shows: CSubUnitDefinition at +0x128, name at +0x68), and +0x490,
# probably its supply level (100000 = fully supplied; 72727 in a starving 23-division stack, 100000 elsewhere),
# and the id of the army (or front) it is in, 0 if none (+0xb0 -> its COrdersGroup, id at +0xc), and the path it
# is moving along ({int province ids} vector at +0x1f0, count +0x1fc: the next province first; battle plans move
# divisions along it without setting the destination at +0x2a4): its length and next province.
# Fixed point x1000; strength 100000 = 100%.
FIELDS = "c1c8 i1e0.a4 i2a4 i20 i30 s3a8.20 s128.68 i490 ib0.c i1fc i1f0.0"


class Division:
    __slots__ = ("id", "owner", "province", "dest", "org", "strength", "template", "unit", "supply", "army", "path",
                 "next")

    def __init__(self, did, owner, province, dest, org, strength, template, unit="", supply=100000, army=0, path=0,
                 nxt=0):
        self.id, self.owner, self.province, self.dest = did, owner, province, dest
        self.org, self.strength, self.template, self.unit, self.supply = org, strength, template, unit, supply
        self.army = army
        self.path, self.next = path, nxt if path else 0  # the next province, 0 when not moving

    @property
    def moving(self):
        return self.dest not in (0, self.province)

    def __repr__(self):
        return "Div(%d %s @%d->%d org %.1f str %.0f%% %s [%s])" % (
            self.id, self.owner, self.province, self.dest, self.org / 1000, self.strength / 1000, self.template,
            self.unit)


class Snapshotter:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def take(self):
        send("bridge.snap reset " + self.path)
        send("bridge.snap now 51 class=CArmy " + FIELDS)
        for attempt in range(20):  # the game may still hold the file for a moment
            try:
                with open(self.path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                break
            except PermissionError:
                time.sleep(0.05)
        else:
            raise PermissionError(self.path)
        out = {}
        if True:
            for line in text.splitlines():
                if line.startswith("#"):
                    continue
                p = line.rstrip("\n").split("\t")
                if len(p) < 7:
                    continue
                try:  # by id: a snapshot written twice (another client switching the snapshot file) counts once
                    out[int(p[0])] = Division(int(p[0]), p[1], int(p[2]), int(p[3]), int(p[4]), int(p[5]), p[6],
                                              p[7] if len(p) > 7 else "", int(p[8]) if len(p) > 8 else 100000,
                                              int(p[9]) if len(p) > 9 and p[9].lstrip("-").isdigit() else 0,
                                              int(p[10]) if len(p) > 11 else 0, int(p[11]) if len(p) > 11 else 0)
                except ValueError:
                    continue
        # strength 0 and org 0: at sea or deploying, not on the map
        return [d for d in out.values() if d.strength or d.org]
