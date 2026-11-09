"""Every division in the game, from one bridge.snap (~15 ms in the game, read back from a file)."""
import os

from hoi4bridge import send

# CArmy (handle 51) fields: owner, province, destination (0 = none), current organisation, strength, template name.
# Fixed point x1000; strength 100000 = 100%.
FIELDS = "c1c8 i1e0.a4 i2a4 i20 i30 s3a8.20"


class Division:
    __slots__ = ("id", "owner", "province", "dest", "org", "strength", "template")

    def __init__(self, did, owner, province, dest, org, strength, template):
        self.id, self.owner, self.province, self.dest = did, owner, province, dest
        self.org, self.strength, self.template = org, strength, template

    @property
    def moving(self):
        return self.dest not in (0, self.province)

    def __repr__(self):
        return "Div(%d %s @%d->%d org %.1f str %.0f%% %s)" % (
            self.id, self.owner, self.province, self.dest, self.org / 1000, self.strength / 1000, self.template)


class Snapshotter:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def take(self):
        send("bridge.snap reset " + self.path)
        send("bridge.snap now 51 class=CArmy " + FIELDS)
        out = []
        with open(self.path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                p = line.rstrip("\n").split("\t")
                if len(p) < 7:
                    continue
                try:
                    out.append(Division(int(p[0]), p[1], int(p[2]), int(p[3]), int(p[4]), int(p[5]), p[6]))
                except ValueError:
                    continue
        # strength 0 and org 0: at sea or deploying, not on the map
        return [d for d in out if d.strength or d.org]
