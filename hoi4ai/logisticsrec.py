"""Logistics settings: each country's fuel prioritization and each army's motorization priority and exercises.

LogisticsLog(path, names, tags, others).step(date, lines) writes to logistics.tsv:
  date  change  country  setting  target  value  was
setting fuel_army / fuel_air / fuel_navy (target empty), motorization or exercise (target the army: its orders group
id and name, e.g. "409 Army 1"); value low / medium / high (exercise: off / on / until_max_xp, on with "stop at max
experience": a click starts exercises, a shift-click until max experience), was the value it replaced. change: start (the first read of a
country), new (an army made later), changed, removed (an army disbanded). Renaming an army shows as removed + new.

Read from a country's save-format block:
  fuel_status: three fuel_consumer_data { index priority }, index 0 army, 1 air, 2 navy; priority 0 low, 1 medium,
      2 high
  theatres / orders_group { id={ id=N type=53 } name=... motorization_level=L }: L 0 low (the default: no trucks;
      a horse on the button), 1 medium (one truck), 2 high (several trucks). The game has no names for the levels.
      training=yes while the army exercises, stop_training_at_max_xp=yes with that box ticked (both absent: off)
Reads at the first call and each new in-game day: TAGS (the recorded countries) each time, OTHERS() (a callable, e.g.
MioLog's producing countries) every FULL_DAYS days, and the country of every logistics command in the record LINES
at once.

The player's commands (in command_record.tsv):
  CSetFuelPriorityCommand             country id +0x28, consumer index +0x2c, priority +0x30
  CSetOrderGroupMotorizationCommand   army handle +0x28 (53:ID), level byte +0x30; the country is the owner column
  COrderSetTrainingCommand            army handle +0x28 (53:ID), exercise byte +0x30, stop at max experience byte
                                      +0x31; the country is the owner column (the AI uses it too)
"""
import os
import re

from hoi4ai import shared
from hoi4bridge import send

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tsetting\ttarget\tvalue\twas"
COMMANDS = {"CSetFuelPriorityCommand": 0x28,           # class -> offset of its country id (None: the owner column)
            "CSetOrderGroupMotorizationCommand": None, "COrderSetTrainingCommand": None}
FUEL_CONSUMERS = ("army", "air", "navy")
LEVELS = ("low", "medium", "high")
_FUEL = re.compile(r"fuel_consumer_data=\{\s*index=(\d+)\s*priority=(\d+)")
_GROUP = re.compile(r"orders_group=\{\s*id=\{ id=(\d+) type=53 \}\s*name=\"([^\"]*)\"")


def parse(line, names):
    """A record line -> the tag of the country a logistics command is for, or None."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] not in COMMANDS:
        return None
    off = COMMANDS[p[2]]
    if off is None:
        return p[3] if p[3] not in ("", "-") else None
    b = bytes.fromhex(p[7])
    return names.tag(int.from_bytes(b[off:off + 4], "little", signed=True)) if len(b) >= off + 4 else None


def _name(names, i):
    i = int(i)
    return names[i] if 0 <= i < len(names) else str(i)


def _block(text, i):
    """The text of the { } block whose opening brace is at or after I."""
    i = text.find("{", i)
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i:j + 1]
    return text[i:]


def settings(text):
    """{(setting, target): value} of one save-format country block."""
    out = {}
    i = text.find("fuel_status={")
    if i >= 0:
        for idx, pri in _FUEL.findall(_block(text, i)):
            out[("fuel_" + _name(FUEL_CONSUMERS, idx), "")] = _name(LEVELS, pri)
    for m in _GROUP.finditer(text):
        blk, army = _block(text, m.start()), f"{m.group(1)} {m.group(2)}"
        lvl = re.search(r"\n\t*motorization_level=(\d+)", blk)
        if lvl:
            out[("motorization", army)] = _name(LEVELS, lvl.group(1))
            out[("exercise", army)] = ("off" if not re.search(r"\n\t*training=yes", blk) else
                                       "until_max_xp" if re.search(r"\n\t*stop_training_at_max_xp=yes", blk) else "on")
    return out


class LogisticsLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(setting, target): value}
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def read(self, tag):
        r = shared.country(tag)
        return settings("\n".join(r)) if r and r[0].startswith("OK") else {}

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        todo = [t for t in (parse(ln, self.names) for ln in lines) if t]
        if day != self.day:
            self.days += 1
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
        for tag in dict.fromkeys(todo):
            now, last = self.read(tag), self.held.get(tag)
            if not now:
                continue
            if last is None:
                rows += [("start", tag) + k + (v, "") for k, v in now.items()]
            else:
                rows += [("new" if k not in last else "changed", tag) + k + (v, last.get(k, ""))
                         for k, v in now.items() if last.get(k) != v]
                rows += [("removed", tag) + k + ("", last[k]) for k in last if k not in now]
            self.held[tag] = now
        self.started = True
        for row in rows:
            self.f.write("\t".join((date,) + row) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
