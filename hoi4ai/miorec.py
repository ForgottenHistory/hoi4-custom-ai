"""Military industrial organisations (MIOs) of every country: size, trait points and the traits unlocked.

MioLog(path, tags).step(date) writes to mios.tsv:
  date  change  country  mio  id  size  points  funds  traits
change: start (every MIO at the first read), changed (size, points or traits differ; funds alone grow every day and
don't count), new / removed (an MIO appears or goes, e.g. from a focus or decision). traits are the unlocked trait
tokens in the order the game lists them; a trait the player picks shows as one more there and one point fewer.

Picking a trait runs no command the bridge sees (CAddIndustrialOrgTraitToQueueCommand is hooked but never ran when
a trait was unlocked, paused or not), so this reads the state: the industrial_organisations of each country's
save-format block (bridge.country, ~15 ms and ~80 KB a country, ~500 KB for a major). TAGS (the recorded countries)
are read each in-game day; every other country with military production lines every FULL_DAYS days (about 85
countries, ~2.5 s). Which countries those are is decided at the first read (all ~440 tags, ~7 s).
A MIO's id is its handle { 79, id } (the manufacturer of production lines, see productionrec).
"""
import os
import re

from hoi4ai import shared
from hoi4bridge import send

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tmio\tid\tsize\tpoints\tfunds\ttraits"
_START = re.compile(r"^\t\t\t\t(\w+)=\{\n\t\t\t\t\tid=\{ id=(\d+) type=79 \}", re.M)


def _all_tags(max_id=1000):
    out = []
    for lo in range(1, max_id, 100):
        reply = send("bridge.tag " + " ".join(str(i) for i in range(lo, lo + 100)))[0]
        out += [kv.split("=")[1] for kv in reply[3:].split() if "=" in kv and not kv.endswith("=?")]
    return out


def parse(text):
    """{MIO id: (token, size, points, funds, traits)} from a country's save-format block."""
    out = {}
    starts = list(_START.finditer(text))
    for k, m in enumerate(starts):
        end = text.find("\n\t\t\t\t}", m.end())
        body = text[m.end():end if end >= 0 else None]
        g = lambda key: (re.search(rf"^\t\t\t\t\t{key}=(\S+)", body, re.M) or [None, ""])[1]
        u = re.search(r"^\t\t\t\t\tunlocked=\{(.*?)^\t\t\t\t\t\}", body, re.M | re.S)
        traits = ",".join(re.findall(r'trait="?(\w+)', u.group(1))) if u else ""
        funds = g("funds")
        try:
            funds = f"{float(funds):.1f}"
        except ValueError:
            pass
        out[int(m.group(2))] = (m.group(1), g("size"), g("points"), funds, traits)
    return out


class MioLog:
    def __init__(self, path, tags=()):
        self.tags = [t for t in tags if t and t != "none"]
        self.others = None          # tags read every FULL_DAYS days
        self.last = {}              # tag -> parse()
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def _read(self, tag):
        r = shared.country(tag)
        return "\n".join(r) if r and r[0].startswith("OK") else ""

    def _diff(self, date, tag, now):
        before = self.last.get(tag)
        rows = []
        for i, v in sorted(now.items()):
            if before is None:
                rows.append(("start", i, v))
            elif i not in before:
                rows.append(("new", i, v))
            elif (v[1], v[2], v[4]) != (before[i][1], before[i][2], before[i][4]):
                rows.append(("changed", i, v))
        if before:
            rows += [("removed", i, before[i]) for i in sorted(before) if i not in now]
        self.last[tag] = now
        for change, i, (tok, size, points, funds, traits) in rows:
            self.f.write("\t".join((date, change, tag, tok, str(i), size, points, funds, traits)) + "\n")
        return len(rows)

    def step(self, date):
        """DATE: "12:00, 1 January, 1936" (the day is the text after the comma) or "1936-01-01 12:00" (before the
        space). Reads once per in-game day. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        if day == self.day:
            return 0
        self.day = day
        n = 0
        if self.others is None:
            self.others = []
            for tag in _all_tags():
                text = self._read(tag)
                if tag in self.tags or re.search(r"^\t\t\tmilitary_lines=", text, re.M):
                    n += self._diff(date, tag, parse(text))
                    if tag not in self.tags:
                        self.others.append(tag)
        else:
            self.days += 1
            todo = self.tags + shared.part(self.others, self.days, FULL_DAYS)
            for tag in todo:
                n += self._diff(date, tag, parse(self._read(tag)))
        self.f.flush()
        return n

    def close(self):
        self.f.close()
