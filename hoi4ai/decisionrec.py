"""Decisions: the decisions each country takes or ignores, and its running and available decisions and missions.

DecisionLog(path, names, tags, others).step(date, lines) writes to decisions.tsv:
  date  change  country  decision  target  value  was
change    value
taken     -             a decision command in the record (CSelectDecisionCommand, CSelectTargetedDecisionCommand)
ignored   -             an ignore command (CIgnoreDecisionCommand, CIgnoreTargetedDecisionCommand); decision is
                        "all" for CIgnoreAllAvailableDecisionCommand; unignored when it took the decision off the
                        ignore list
start, new, changed, removed
          the state     the country's decision_status: a running decision or mission (`active N days`), one
                        cooling down (`re_enable_cooldown N days`) or an available targeted decision (`available`,
                        `available ignored`); the days left are only written, a change of state is what counts;
                        `ignored` for a decision on the country's ignore list (player_settings)

The commands hold the country id at +0x28 (and a targeted decision's target id at +0x2c) and pointers to the
decision: a CDecision (its name a string at +0x120) or a CTargetedDecision (its CDecision at +0x10). Every
pointer in the record is tried; an ignore command's pointer is no decision, so its name is the one added to (or
taken off) the ignore list by the read right after it. Commands are written for TAGS only (the AI takes decisions
all the time; the other countries' show in their status).

The status is the `decision_status={ active_timed_decision={ decision= days= state= }
active_targeted_decision={ decision= target= ignore= days= state= } random_item={...} }` and
`player_settings={ ignore_decisions={ NAME ... } }` parts of the country's save-format block (bridge.country). Reads at the first call and each new in-game day: TAGS each time, OTHERS()
spread over FULL_DAYS days; and TAGS at once after a decision command in the record LINES.
"""
import os
import re
import struct

from hoi4ai import pdx
from hoi4ai import shared

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tdecision\ttarget\tvalue\twas"
TAKE = ("CSelectDecisionCommand", "CSelectTargetedDecisionCommand")
IGNORE = ("CIgnoreDecisionCommand", "CIgnoreTargetedDecisionCommand", "CIgnoreAllAvailableDecisionCommand")
COMMANDS = TAKE + IGNORE
NAME = 0x120        # CDecision: the name (a string)
TARGETED = 0x10     # CTargetedDecision: its CDecision
_STATUS = re.compile(r"\n\t\tdecision_status=\{(.*?)\n\t\t\}", re.S)
_IGNORED = re.compile(r"\n\t\tplayer_settings=\{.*?\n\t\t\tignore_decisions=\{(.*?)\}", re.S)


def is_command(line):
    p = line.split("\t", 3)
    return len(p) > 2 and p[2] in COMMANDS


def status(body):
    """{(decision, target, ""): (state, days)} from the text inside decision_status={ }."""
    out = {}
    for k, _, v in pdx.parse(body):
        if k in ("active_timed_decision", "active_targeted_decision") and isinstance(v, list):
            state = pdx.get(v, "state", "?")
            if pdx.get(v, "ignore") == "yes":
                state += " ignored"
            out[(pdx.get(v, "decision", "?"), pdx.get(v, "target", ""), "")] = (state, pdx.get(v, "days", ""))
    return out


class DecisionLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(decision, target): (state, days)}
        self.decisions = {}         # CDecision / CTargetedDecision address -> name
        self.proc = None
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def decision(self, a):
        """The name of the decision at A, or None when A is no decision."""
        if a in self.decisions:
            return self.decisions[a]
        from hoi4ai.memory import Process
        from hoi4ai.templates import _string
        if self.proc is None:
            self.proc = Process()
        cls = self.proc.class_of(a)
        name = None
        if cls == "CDecision":
            name = _string(self.proc, a + NAME) or None
        elif cls == "CTargetedDecision":
            d = self.proc.u64(a + TARGETED)
            name = _string(self.proc, d + NAME) or None if self.proc.class_of(d) == "CDecision" else None
        self.decisions[a] = name
        return name

    def command(self, line):
        """A decision command record line -> (change, country, decision, target), else None."""
        p = line.rstrip("\n").split("\t")
        if len(p) < 8 or p[2] not in COMMANDS:
            return None
        b = bytes.fromhex(p[7].split()[0])
        if len(b) < 0x30:
            return None
        who, whom = struct.unpack_from("<ii", b, 0x28)
        change = "taken" if p[2] in TAKE else "ignored"
        if p[2] == "CIgnoreAllAvailableDecisionCommand":
            return change, self.names.tag(who), "all", ""
        name = None
        for off in range(0x30, min(len(b), 0x60) - 7, 8):
            v = struct.unpack_from("<Q", b, off)[0]
            if 0x10000 < v < 1 << 47:
                name = self.decision(v)
                if name:
                    break
        target = self.names.tag(whom) if "Targeted" in p[2] else ""
        if p[2] in IGNORE:
            name = None     # its pointer is no CDecision; named from the ignore list (step)
        return change, self.names.tag(who), name or "?", target

    def read(self, tag):
        r = shared.country(tag)
        if not r or not r[0].startswith("OK"):
            return None
        text = "\n".join(r).replace("\r", "")
        m = _STATUS.search(text)
        out = status(m.group(1)) if m else {}
        m = _IGNORED.search(text)
        for name in (m.group(1).split() if m else ()):
            out[(name.strip('"'), "", "ignored")] = ("ignored", "")
        return out

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        acted = [ln for ln in lines if is_command(ln)]
        todo = list(self.tags) if acted else []
        if day != self.day:
            self.days += 1
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
        unnamed = {}        # tag -> [row index] of ignore commands still to name
        for ln in acted:
            c = self.command(ln)
            if c and c[1] in self.tags:
                if c[2] == "?" and c[0] == "ignored":
                    unnamed.setdefault(c[1], []).append(len(rows))
                rows.append([c[0], c[1], c[2], c[3], "", ""])
        for tag in dict.fromkeys(todo):
            now, last = self.read(tag), self.held.get(tag)
            if now is None:
                continue
            for k, (state, days) in sorted(now.items()):
                value = f"{state} {days} days" if days not in ("", "0", "-1") else state
                if last is None:
                    rows.append(("start", tag, k[0], k[1], value, ""))
                elif k not in last:
                    rows.append(("new", tag, k[0], k[1], value, ""))
                elif last[k][0] != state:
                    rows.append(("changed", tag, k[0], k[1], value, last[k][0]))
            for k in sorted(last or ()):
                if k not in now:
                    rows.append(("removed", tag, k[0], k[1], "", last[k][0]))
            if last is not None and tag in unnamed:      # what the ignore commands put on or took off the list
                found = ([("ignored", k[0]) for k in now if k[2] and k not in last]
                         + [("unignored", k[0]) for k in last if k[2] and k not in now])
                for i, (change, name) in zip(unnamed[tag], found):
                    rows[i][0], rows[i][2] = change, name
            self.held[tag] = now
        self.started = True
        for row in rows:
            self.f.write("\t".join((date,) + tuple(str(x) for x in row)) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
