"""Sub-unit (battalion) types from common/units (through the active mods): which ones make a division fast.

A division's main sub-unit type (the one its map counter shows) is read live (CArmy +0x128 -> its
CSubUnitDefinition, name at +0x68). Fast: group = armor, or group = mobile with a transport (motorized,
mechanized) or no speed malus (armoured cars); cavalry (mobile, maximum_speed 0.6) is not fast.
"""
from . import pdx
from .gamefiles import GameFiles


class SubUnitType:
    __slots__ = ("name", "group", "transport", "speed_factor", "categories")

    def __init__(self, name, group, transport, speed_factor, categories):
        self.name, self.group, self.transport = name, group, transport
        self.speed_factor, self.categories = speed_factor, categories

    @property
    def armored(self):
        return self.group == "armor"

    @property
    def fast(self):
        if self.group == "armor":
            return True
        return self.group == "mobile" and (self.transport is not None or self.speed_factor >= 1.0)


def load(files=None):
    files = files or GameFiles()
    out = {}
    for name, path in sorted(files.listdir("common/units", ".txt").items()):
        tree = pdx.parse(open(path, encoding="utf-8-sig", errors="replace").read())
        for unit_name, _, body in pdx.get(tree, "sub_units", []) or []:
            if not isinstance(body, list):
                continue
            try:
                speed = float(pdx.get(body, "maximum_speed", "1"))
            except ValueError:
                speed = 1.0
            cats = pdx.values(pdx.get(body, "categories", []) or [])
            out[unit_name] = SubUnitType(unit_name, pdx.get(body, "group"), pdx.get(body, "transport"), speed, cats)
    return out
