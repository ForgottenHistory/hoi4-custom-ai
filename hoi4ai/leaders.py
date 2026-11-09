"""Unit leaders (generals, field marshals) of a country, read from the running game with one bridge.snap.

CArmyLeader (handle type 4713, as in save files: id = { id = 380 type = 4713 }): display name +0x40, key +0x20,
country +0x120, kind +0xe7c (0 field marshal, 1 general), current skills attack +0xf58, defence +0xf68,
planning +0xf78, logistics +0xf88. Verified 2026-11-09 against common/characters/GER.txt (defence and logistics
equal; attack and planning include modifiers).
"""
import os

from hoi4bridge import send

FIELDS = "c120 ie7c if58 if68 if78 if88 s40 s20"
FIELD_MARSHAL, GENERAL = 0, 1


class Leader:
    __slots__ = ("id", "owner", "kind", "attack", "defense", "planning", "logistics", "name", "key")

    def __init__(self, lid, owner, kind, attack, defense, planning, logistics, name, key):
        self.id, self.owner, self.kind = lid, owner, kind
        self.attack, self.defense, self.planning, self.logistics = attack, defense, planning, logistics
        self.name, self.key = name, key

    @property
    def field_marshal(self):
        return self.kind == FIELD_MARSHAL

    def __repr__(self):
        return "%s(%d %s atk %d def %d plan %d log %d)" % (
            "FM" if self.field_marshal else "Gen", self.id, self.name, self.attack, self.defense, self.planning,
            self.logistics)


def load(tag, path):
    """Every army leader of country TAG (snapshot written to PATH)."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    send("bridge.snap reset " + path)
    send("bridge.snap leaders 4713 class=CArmyLeader " + FIELDS)
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 9 or p[1] != tag:
                continue
            try:
                out.append(Leader(int(p[0]), p[1], int(p[2]), int(p[3]), int(p[4]), int(p[5]), int(p[6]), p[7], p[8]))
            except ValueError:
                continue
    return out
