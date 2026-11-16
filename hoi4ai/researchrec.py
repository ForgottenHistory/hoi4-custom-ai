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

Also facilities.tsv (same reads), the research facilities and special projects of program_status={ }:
  date  change  country  kind  name  province  scientist  state  was
kind "facility" (name the building, e.g. land_facility; province its province; scientist
the scientist id, empty when none; state "id=N project=NAME dismantle=current/target[ timed]", timed while a dismantle runs) or "project" (name the sp_ project,
shown while started or completed; state "start=yes completed=no program=ID current_state=N iterations=N[ multiple_choice=NAME][ flags=..]": a multiple_choice
is a choice waiting for an answer, gone once taken). change: start / new /
removed / changed as above; kind "reward" is one entry of a project's history (name the project, province, scientist its character id,
state "option=NAME reward=NAME progress=N date=D": a learning or a choice taken, once, as a new row). was: the compared fields (scientist, state without progress) as field=old.
Read from program_status={ program={ id={ id type=83 } province scientist={ } delayed_dismantle_facility={ target
current } building } project_pool={ project={ NAME={ progress={ prototype_state={ project_progress iterations } }
start completed } } } }.

The commands (in command_record.tsv; the owner column is "-" for every country, the country id is at +0x28):
  CSetResearchCommand     country id +0x28, technology (a CTechnologyTemplate*, name std::string at +0x10) +0x30,
                          slot +0x38 (0 the first), +0x3c 0x100 (0x10100 seen when switching back to a started tech)
  CSetDesignTeamCommand   technology +0x30, design company 79:ID +0x38
"""
import os
import re

from hoi4ai import shared
from hoi4bridge import send

FULL_DAYS = 7
FHEADER = "date\tchange\tcountry\tkind\tname\tprovince\tscientist\tstate\twas"
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


def _flat(s):
    """S on one line, whitespace collapsed."""
    return re.sub(r"\s+", " ", s).strip()


def facilities(text):
    """{(kind, key): {field: value}} of the facilities and started or finished special projects of one country block."""
    i = text.find("\n\t\tprogram_status={")
    if i < 0:
        return {}
    out = {}
    for name, b in _entries(_block(text, i + 1)):
        if name == "program":
            pid = re.search(r"id=\{ id=(\d+) type=83 \}", b)
            pid = pid.group(1) if pid else str(len(out))
            parts = dict(_entries(b))
            sci = re.search(r"scientist=\{ id=(\d+) type=73 \}", parts.get("scientist", ""))
            dis, una = parts.get("delayed_dismantle_facility", ""), parts.get("scientist", "")
            una = re.search(r"delayed_unassigned_scientist=\{(.*?)\}", una, re.S)
            una = una.group(1) if una else ""
            out["facility", pid] = {
                "name": _get(b, "building"), "province": _get(b, "province"),
                "scientist": sci.group(1) if sci else "",
                "state": f"id={pid} project={_get(b, 'project')} dismantle={_get(dis, 'current')}/{_get(dis, 'target')}"
                         f"{' timed' if _get(dis, 'timed') == 'yes' else ''}"
                         + (f" unassign={_get(una, 'current')}/{_get(una, 'target')}" if sci and _get(una, "timed") == "yes" else "")}
        elif name == "project_pool":
            for n, body in _entries(_block(b, 1)):
                if _get(body, "start") == "yes" or _get(body, "completed") == "yes":
                    parts = dict(_entries(body))
                    ps = dict(_entries(parts.get("progress", ""))).get("prototype_state", "")
                    extra = " ".join(f"{k}={_flat(parts[k])}" for k in ("variables", "flags") if _flat(parts.get(k, "")) not in ("", "{ }"))
                    prog = re.search(r"id=(\d+)", parts.get("program", ""))
                    hist = parts.get("history", "")
                    k, pos = 0, 1
                    while "{" in hist[pos:]:
                        h = _block(hist, pos)
                        pos = hist.find(h, pos) + len(h)
                        k += 1
                        if _get(h, "option") in ("", "undefined"):      # the game's empty placeholder entry
                            continue
                        ctx = dict(_entries(h)).get("context", "")
                        char = re.search(r"character=\{ id=(\d+)", ctx)
                        out["reward", f"{n}#{k}"] = {
                            "name": n, "province": _get(ctx, "province"), "scientist": char.group(1) if char else "",
                            "state": f"option={_get(h, 'option')} reward={_get(h, 'prototype_reward')} "
                                     f"progress={_get(ctx, 'progress')} date={game_date(_get(h, 'date'))}"}
                    out["project", n] = {
                        "name": n, "province": "", "scientist": "", "progress": _get(ps, "project_progress"),
                        "state": f"start={_get(body, 'start')} completed={_get(body, 'completed')} "
                                 f"program={prog.group(1) if prog else ''} "
                                 f"current_state={_get(parts.get('progress', ''), 'current_state')} "
                                 f"iterations={_get(ps, 'iterations')}"
                                 + (f" multiple_choice={_get(body, 'multiple_choice')}" if _get(body, "multiple_choice") else "")
                                 + (" " + extra if extra else "")}
    return out


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

        fpath = os.path.join(os.path.dirname(path), "facilities.tsv")
        new = not os.path.exists(fpath)
        self.ff = open(fpath, "a", encoding="utf-8")
        if new:
            self.ff.write(FHEADER + "\n")
        self.fheld = {}             # tag -> {(kind, key): fields}

    def read(self, tag):
        r = shared.country(tag)
        text = "\n".join(r) if r and r[0].startswith("OK") else ""
        return research(text) + (facilities(text),)

    def _fdiff(self, tag, now):
        last = self.fheld.get(tag)
        rows = []
        for key, v in sorted(now.items()):
            old = None if last is None else last.get(key)
            row = (tag, key[0], v["name"], v["province"], v["scientist"], v["state"])
            if old is None:
                rows.append(("start" if last is None else "new",) + row + ("",))
            elif (old["scientist"], old["state"]) != (v["scientist"], v["state"]):
                rows.append(("changed",) + row + (";".join(f"{f}={old[f]}" for f in ("scientist", "state") if old[f] != v[f]),))
        if last is not None:
            rows += [("removed", tag, k[0], v["name"], v["province"], v["scientist"], v["state"], "")
                     for k, v in sorted(last.items()) if k not in now]
        self.fheld[tag] = now
        return rows

    def _diff(self, tag, now):
        slots, done = now[:2]
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
        self.held[tag] = now[:2]
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
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows, frows = [], []
        for tag in dict.fromkeys(todo):
            now = self.read(tag)
            rows += self._diff(tag, now)
            frows += self._fdiff(tag, now[2])
        self.started = True
        for row in rows:
            self.f.write("\t".join((date,) + row) + "\n")
        for row in frows:
            self.ff.write("\t".join((date,) + row) + "\n")
        self.f.flush()
        self.ff.flush()
        return len(rows) + len(frows)

    def close(self):
        self.f.close()
        self.ff.close()
