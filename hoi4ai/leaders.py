"""Unit leaders (generals, field marshals) of a country, read from the running game with one bridge.snap.

CArmyLeader (handle type 4713, as in save files: id = { id = 380 type = 4713 }): display name +0x40, key +0x20,
country +0x120, current role +0xe7c (0 field marshal, 1 general: Rundstedt went from 1 to 0 when he took an army
group), can-be-field-marshal flag +0xde8 (1 for Rundstedt and Kesselring in 1939, the two field marshals the game
lists; 0 for everyone else), current skills attack +0xf58, defence +0xf68, planning +0xf78,
logistics +0xf88. Verified 2026-11-09 against common/characters/GER.txt (defence and logistics equal; attack and
planning include modifiers).

Leaders with role 0 but no flag (Mackensen, Blomberg, Mueller in 1939, which the game doesn't list) are left out: the game removes them from the country again when they're given a command (seen with a watchpoint on
an army group's leader: its "remove leader" routine unassigns them within hours).
"""
import os

from hoi4bridge import send

FIELDS = "c120 ie7c if58 if68 if78 if88 bde8 s40 s20"
GENERAL = 1


class Leader:
    __slots__ = ("id", "owner", "kind", "attack", "defense", "planning", "logistics", "marshal", "name", "key")

    def __init__(self, lid, owner, kind, attack, defense, planning, logistics, marshal, name, key):
        self.id, self.owner, self.kind, self.marshal = lid, owner, kind, marshal
        self.attack, self.defense, self.planning, self.logistics = attack, defense, planning, logistics
        self.name, self.key = name, key

    @property
    def field_marshal(self):
        return bool(self.marshal)

    def __repr__(self):
        return "%s(%d %s atk %d def %d plan %d log %d)" % (
            "FM" if self.field_marshal else "Gen", self.id, self.name, self.attack, self.defense, self.planning,
            self.logistics)


def load(tag, path):
    """Every army leader country TAG can use (snapshot written to PATH)."""
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
            if len(p) < 10 or p[1] != tag:
                continue
            if p[7] != "1" and p[2] != str(GENERAL):  # neither a field marshal nor a general: not usable
                continue
            try:
                out.append(Leader(int(p[0]), p[1], int(p[2]), int(p[3]), int(p[4]), int(p[5]), int(p[6]), int(p[7]),
                                  p[8], p[9]))
            except ValueError:
                continue
    return out
