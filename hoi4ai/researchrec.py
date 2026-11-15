"""Research: what each research slot of a country works on, with which design company, and the techs finished.

ResearchLog(path, names, tags, others).step(date, lines) writes to research.tsv:
  date  change  country  slot  tech  level  design_team  points  was
slot the research slot (1 the first), tech the technology it researches (empty: an idle slot), design_team the MIO
that helps (handle 79 id and name, e.g. "23 GER_mauser_organization"), points the research points put in so far
(written, not compared). change: start (the first read of a country, one row per slot), changed (another tech or
design company in a slot), new / removed (a slot gained or lost), done (a tech finished: slot empty, level its new
level, design_team the company that helped, points the game's date it finished, in DATE's format: rows are dated
when read, which at high speed can be weeks later). was: what changed, as field=old.

Reads at the first call and each new in-game day: TAGS (the recorded countries) each time, OTHERS() (a callable, e.g.
MioLog's producing countries) every FULL_DAYS days, and TAGS at once after a research command in the record LINES
that is theirs (CSetResearchCommand's country id; CSetDesignTeamCommand names none, so any of these).

Read from a country's save-format block: technology={ technologies={ NAME={ level research_points date design_team={
id type=79 } } } slots={ NAME={ ... } | empty={ points } } }, the slots in order; MIO names from
production={ industrial_organisations={ NAME={ id={ id type=79 } } } }.

The commands (in command_record.tsv; the owner column is "-" for every country, the country id is at +0x28):
  CSetResearchCommand     country id +0x28, technology (a CTechnologyTemplate*, name std::string at +0x10) +0x30,
                          slot +0x38 (0 the first), +0x3c 0x100 (0x10100 seen when switching back to a started tech)
  CSetDesignTeamCommand   technology +0x30, design company 79:ID +0x38
"""
import os
import re

from hoi4bridge import send

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tslot\ttech\tlevel\tdesign_team\tpoints\twas"
FIELDS = ("tech", "level", "design_team", "points")
COMPARE = ("tech", "design_team")
COMMANDS = ("CSetResearchCommand", "CSetDesignTeamCommand")
_MIO = re.compile(r"^\t\t\t\t(\w+)=\{\n\t\t\t\t\tid=\{ id=(\d+) type=79 \}", re.M)


def _block(text, i):
    """The text of the { } block whose opening brace is at or after I."""
    i = text.find("{", i)
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i:j + 1]
    return text[i:]


def _entries(blk):
    """[(name, body)] of the depth-1 NAME={ } entries of block BLK."""
    out, i = [], 1
    for m in re.finditer(r"\n\t*(\w+)=\{", blk):
        if m.start() < i:
            continue
        body = _block(blk, m.end() - 1)
        out.append((m.group(1), body))
        i = m.end() - 1 + len(body)
    return out


def _get(text, key):
    m = re.search(r"\n\t*" + key + r"=(\"[^\"]*\"|[^\s{}]*)", text)
    return m.group(1).strip('"') if m else ""


def research(text):
    """({slot: {field: value}}, {tech: (level, points)}) of one save-format country block."""
    i = text.find("\n\t\ttechnology={")
    if i < 0:
        return {}, {}
    tb = _block(text, i + 1)
    mios = {m.group(2): m.group(1) for m in _MIO.finditer(text)}
    j = tb.find("\n\t\t\ttechnologies={")
    techs = dict(_entries(_block(tb, j + 1))) if j >= 0 else {}
    j = tb.find("\n\t\t\tslots={")
    slots = {}
    for k, (name, _) in enumerate(_entries(_block(tb, j + 1)) if j >= 0 else (), 1):
        t = techs.get(name, "") if name != "empty" else ""
        team = re.search(r"\n\t*design_team=\{ id=(\d+) type=79 \}", t) if t else None
        slots[k] = {"tech": "" if name == "empty" else name, "level": "",
                    "design_team": f"{team.group(1)} {mios.get(team.group(1), '')}".strip() if team else "",
                    "points": _get(t, "research_points") if t else ""}
    done = {}
    for n, b in techs.items():
        if _get(b, "level"):
            team = re.search(r"design_team=\{ id=(\d+) type=79 \}", b)
            done[n] = (_get(b, "level"), _get(b, "date"),
                       f"{team.group(1)} {mios.get(team.group(1), '')}".strip() if team else "")
    return slots, done


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def game_date(d):
    """A save date "1936.4.15.1" -> "1:00, 15 April, 1936" (cmdrec's format), else D."""
    p = d.split(".")
    if len(p) == 4 and all(x.isdigit() for x in p) and 1 <= int(p[1]) <= 12:
        return f"{int(p[3])}:00, {int(p[2])} {MONTHS[int(p[1]) - 1]}, {p[0]}"
    return d


def parse(line):
    """A record line -> (class, country id or None) of a research command, or None. CSetDesignTeamCommand names no
    country."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] not in COMMANDS:
        return None
    h = re.match(r"[0-9A-Fa-f]*", p[7]).group(0)
    b = bytes.fromhex(h[:len(h) // 2 * 2])
    return p[2], int.from_bytes(b[0x28:0x2c], "little") if p[2] == "CSetResearchCommand" and len(b) >= 0x2c else None


class ResearchLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> (slots, done)
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def read(self, tag):
        r = send(f"bridge.country {tag}")
        return research("\n".join(r)) if r and r[0].startswith("OK") else ({}, {})

    def _diff(self, tag, now):
        slots, done = now
        last = self.held.get(tag)
        rows = []
        for k, v in sorted(slots.items()):
            old = None if last is None else last[0].get(k)
            row = (tag, str(k)) + tuple(v[f] for f in FIELDS)
            if old is None:
                rows.append(("start" if last is None else "new",) + row + ("",))
            elif any(old[f] != v[f] for f in COMPARE):
                rows.append(("changed",) + row + (";".join(f"{f}={old[f]}" for f in COMPARE if old[f] != v[f]),))
        if last is not None:
            rows += [("removed", tag, str(k)) + tuple(v[f] for f in FIELDS) + ("",)
                     for k, v in sorted(last[0].items()) if k not in slots]
            for n, (level, when, team) in sorted(done.items()):
                was = last[1].get(n, ("",))[0]
                if level != was:
                    rows.append(("done", tag, "", n, level, team, game_date(when), f"level={was}" if was else ""))
        self.held[tag] = now
        return rows

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        todo = []
        for cls, cid in filter(None, map(parse, lines)):
            todo += self.tags if cid is None else [t for t in self.tags if self.names.tag(cid) == t]
        if day != self.day:
            self.days += 1
            todo += self.tags
            if not self.started or self.days % FULL_DAYS == 0:
                todo += list(self.others() or ())
        self.day = day
        rows = []
        for tag in dict.fromkeys(todo):
            rows += self._diff(tag, self.read(tag))
        self.started = True
        for row in rows:
            self.f.write("\t".join((date,) + row) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
