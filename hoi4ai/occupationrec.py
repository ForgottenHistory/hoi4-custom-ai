"""Occupation: each country's occupation laws and garrison templates, globally, per occupied country and per state.

OccupationLog(path, names, tags, others).step(date, lines) writes to occupation.tsv:
  date  change  country  kind  target  key  value  was
kind      target               key                    value
global    -                    default_law            the law for occupied countries without their own
global    -                    template               the primary garrison template (NAME #id)
global    -                    priority               the garrison priority
country   the occupied country the key (law, template, the country's own setting
                               ...)
state     the state id         law                    the state's occupation law (its own, else its country's)
state     the state id         template, ...          the state's own garrison template and other settings
release   the released country options                the release panel's check boxes: puppet, play_as,
                                                      retain_cores (retain the states with our cores), or none
change: start (the first read of a country), new, changed (was the old value), removed (also when a state is no
longer occupied); released for a release, from the command itself (CReleaseCountryCommand: +0x28 the releasing
country id, +0x2c the released, +0x30 play as, +0x31 puppet, +0x32 retain cores, one byte each).

All of it is the `occupation_status={ default_law= priority= division_template_id= occupation={ TAG={ states={...}
occupation_law_list={ STATE=law } state_garrison_data={ STATE={...} } ... } } }` part of the country's save-format
block (bridge.country). Resistance, compliance and the garrison's strength and equipment change on their own and are
left out. A template is named from `bridge.write 52 ID`. Reads at the first call and each new in-game day: TAGS
each time, OTHERS() spread over FULL_DAYS days; and TAGS at once after an occupation command in the record LINES
(COMMANDS).
"""
import os
import re

from hoi4ai import pdx
from hoi4ai import shared
from hoi4bridge import BridgeError, BridgeUnavailable, send

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tkind\ttarget\tkey\tvalue\twas"
COMMANDS = ("CSetOccupationPolicyCommand", "CSetDefaultCountryOccupationPolicyCommand",
            "CSetStateOverrideOccupationPolicyCommand", "CSetMainGarrisonTemplateCommand",
            "CSetCountryGarrisonTemplateCommand", "CSetStateGarrisonTemplateCommand",
            "CSetCountryGarrisonPriorityCommand", "CReleaseCountryCommand")
VOLATILE = {"resistance", "compliance", "strength_ratio", "garrison_required", "army_manpower_need", "manpower_pool",
            "equipment", "equipment_need", "garrison_reinforcement_requests", "compliance_modifiers",
            "resistance_modifiers", "states", "occupation_law_list", "state_garrison_data", "state_compliance_cache"}
RELEASE = "CReleaseCountryCommand"
RELEASE_FLAGS = ((0x30, "play_as"), (0x31, "puppet"), (0x32, "retain_cores"))
_OCCUPATION = re.compile(r"\n\t\toccupation_status=\{(.*?)\n\t\t\}", re.S)


def flat(v):
    if not isinstance(v, list):
        return str(v)
    return " ".join(k if op is None and not isinstance(k, list) else
                    f"{k}={'{' + flat(x) + '}' if isinstance(x, list) else x}" for k, op, x in v)


def is_command(line):
    p = line.split("\t", 3)
    return len(p) > 2 and p[2] in COMMANDS


def release(line, names):
    """A CReleaseCountryCommand record line -> (releasing tag, released tag, options), else None.
    Country ids: +0x28 the one releasing, +0x2c the released; the panel's check boxes are bytes +0x30..+0x32."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] != RELEASE:
        return None
    b = bytes.fromhex(p[7].split()[0])
    if len(b) < 0x34:
        return None
    who, whom = (int.from_bytes(b[o:o + 4], "little") for o in (0x28, 0x2c))
    return names.tag(who), names.tag(whom), " ".join(n for o, n in RELEASE_FLAGS if b[o]) or "none"


class OccupationLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(kind, target, key): value}
        self.templates = {}         # template id -> name
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def template(self, v):
        """`{ id=N type=52 }` -> "NAME #N"."""
        tid = pdx.get(v, "id") if isinstance(v, list) else v
        if tid not in self.templates:
            name = ""
            try:
                r = send(f"bridge.write 52 {tid}")
            except (BridgeError, BridgeUnavailable):
                r = None
            if r and r[0].startswith("OK"):
                m = re.search(r'\n\tname="([^"]*)"', "\n".join(r))
                name = m.group(1) if m else ""
                self.templates[tid] = name
            return f"{name} #{tid}".strip()
        return f"{self.templates[tid]} #{tid}".strip()

    def settings(self, block, kind, target, out):
        """The scalar settings of BLOCK (and its garrison template) as (KIND, TARGET, key) rows."""
        for k, _, v in block:
            if k in VOLATILE:
                continue
            if k == "division_template_id":
                out[(kind, target, "template")] = self.template(v)
            else:
                out[(kind, target, k)] = flat(v)[:300]

    def parse(self, body):
        out = {}
        top = pdx.parse(body)
        self.settings([x for x in top if x[0] != "occupation"], "global", "", out)
        for occupied, _, block in pdx.get(top, "occupation") or ():
            if not isinstance(block, list):
                continue
            self.settings(block, "country", occupied, out)
            for state, _, law in pdx.get(block, "occupation_law_list") or ():
                out[("state", state, "law")] = law
            for state, _, data in pdx.get(block, "state_garrison_data") or ():
                if isinstance(data, list):
                    self.settings(data, "state", state, out)
        return out

    def read(self, tag):
        r = shared.country(tag)
        if not r or not r[0].startswith("OK"):
            return None
        m = _OCCUPATION.search("\n".join(r).replace("\r", ""))
        return self.parse(m.group(1)) if m else {}

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        todo = list(self.tags) if any(is_command(ln) for ln in lines) else []
        if day != self.day:
            self.days += 1
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
        for ln in lines:
            r = release(ln, self.names)
            if r:
                rows.append(("released", r[0], ("release", r[1], "options"), r[2], ""))
        for tag in dict.fromkeys(todo):
            now, last = self.read(tag), self.held.get(tag)
            if now is None:
                continue
            if last is None:
                rows += [("start", tag, k, v, "") for k, v in sorted(now.items())]
            else:
                rows += [("new" if k not in last else "changed", tag, k, v, last.get(k, ""))
                         for k, v in sorted(now.items()) if last.get(k) != v]
                rows += [("removed", tag, k, "", last[k]) for k in sorted(last) if k not in now]
            self.held[tag] = now
        self.started = True
        for change, tag, (kind, target, key), value, was in rows:
            self.f.write("\t".join((date, change, tag, kind, target, key, str(value), str(was))) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
