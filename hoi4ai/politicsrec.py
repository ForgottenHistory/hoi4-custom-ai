"""National focus, laws, advisors and national spirits: what each country has picked.

PoliticsLog(path, names, tags, others).step(date, lines) writes to politics.tsv:
  date  change  country  kind  slot  value  was
kind      slot                                   value
focus     current                                the focus being researched (removed when it ends: completed or
                                                 cancelled)
focus     paused                                 yes while the current focus is paused
focus     completed                              a completed focus (start rows list those done before)
law       the law category (economy,             the law
          trade_laws, mobilization_laws, ...)
advisor   the slot (political_advisor, theorist, the advisor's character token (GER_hjalmar_schacht)
          high_command, army_chief, ...)
idea      the idea category (country, ...)       any other idea: national spirits, manufacturers (officer corps
                                                 spirits are in spirits.tsv)
change: start (the first read of a country), new, changed (a law replaced; was the old one), removed.

All of it is in the country's save-format block (bridge.country): `focus={ completed="..." progress= current="..."
paused= }`, `ideas={ ... }` (laws are the ideas of the common/ideas categories with law = yes) and
`appointed_advisors={ { slot="..." character={ id=N type=73 } } ... }`; an advisor's token is read once from its
character (`bridge.write 73 ID`: token=, country=). Reads at the first call and each new in-game day: TAGS each
time, OTHERS() (a callable, e.g. MioLog's producing countries) spread over FULL_DAYS days, and at once the country
of every command in the record LINES:
  CSetNationalFocusCommand   country id +0x28, CNationalFocus* +0x30: picking a focus
  CDropCurrentNationalFocusCommand  country id +0x28: cancelling the current focus
  CAddIdeaCommand            country id +0x28, CIdea* +0x30: a law, or an idea in an empty slot
  CReplaceIdeaCommand        country id +0x28, old CIdea* +0x30, new CIdea* +0x38: changing a law
  CAddAdvisorCommand         character handle 73:ID at +0x28 (the country is the character's): hiring an advisor
  CRemoveAdvisorCommand      the same: dismissing one
"""
import os
import re

from hoi4ai import pdx
from hoi4ai import shared
from hoi4bridge import BridgeError, BridgeUnavailable, send

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tkind\tslot\tvalue\twas"
COUNTRY_AT_28 = ("CSetNationalFocusCommand", "CDropCurrentNationalFocusCommand", "CAddIdeaCommand",
                 "CReplaceIdeaCommand")
ADVISORS = ("CAddAdvisorCommand", "CRemoveAdvisorCommand")
_IDEAS = re.compile(r"\n\t*ideas=\{([^}]*)\}")
_FOCUS = re.compile(r"\n\t*focus=\{([^}]*)\}")
_ADVISORS = re.compile(r"\n\t*appointed_advisors=\{(.*?)\n\t*\}", re.S)
_APPOINTED = re.compile(r'slot="([^"]+)"\s*character=\{\s*id=(\d+)')


def game_ideas(files=None):
    """({idea key: category}, {law category}) from the game files' common/ideas."""
    from hoi4ai.gamefiles import GameFiles
    files = files or GameFiles()
    ideas, laws = {}, set()
    for path in files.listdir("common/ideas", ".txt").values():
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for top, _, body in pdx.parse(f.read()):
                if top != "ideas" or not isinstance(body, list):
                    continue
                for cat, _, v in body:
                    if not isinstance(v, list):
                        continue
                    if any(k == "law" and x == "yes" for k, _, x in v):
                        laws.add(cat)
                    for key, _, x in v:
                        if isinstance(x, list):
                            ideas[key] = cat
    return ideas, laws


def parse(line, names, characters=None):
    """A record line -> the tag of the country a focus, law or advisor command is for, or None."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or (p[2] not in COUNTRY_AT_28 and p[2] not in ADVISORS):
        return None
    b = bytes.fromhex(p[7])
    if len(b) < 0x30:
        return None
    n = int.from_bytes(b[0x28:0x2c], "little", signed=True)
    if p[2] not in ADVISORS:
        return names.tag(n)
    if characters is None:
        return None
    return characters(int.from_bytes(b[0x2c:0x30], "little", signed=True))[1]


class PoliticsLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        from hoi4ai.spiritrec import game_spirits
        self.names = names or Names()
        self.ideas, self.laws = game_ideas()
        self.spirits = game_spirits()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(kind, slot, key): value}
        self.chars = {}             # character id -> (token, country tag)
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def character(self, cid):
        """(token, country tag) of character CID, read once."""
        if cid not in self.chars:
            token, tag = str(cid), ""
            try:
                r = send(f"bridge.write 73 {cid}")
            except (BridgeError, BridgeUnavailable):
                r = None
            if r and r[0].startswith("OK"):
                text = "\n".join(r)
                m = re.search(r'\n\ttoken="([^"]*)"', text)
                token = m.group(1) if m else token
                m = re.search(r'\n\tcountry="([^"]*)"', text)
                tag = m.group(1) if m else tag
                self.chars[cid] = (token, tag)
            else:
                return token, tag
        return self.chars[cid]

    def read(self, tag):
        """{(kind, slot, key): value}, or None when the block can't be read."""
        r = shared.country(tag)
        if not r or not r[0].startswith("OK"):
            return None
        text = "\n".join(r)
        out = {}
        m = _FOCUS.search(text)
        if m:
            body = m.group(1)
            for f in re.findall(r'completed="([^"]*)"', body):
                out[("focus", "completed", f)] = f
            c = re.search(r'current="([^"]*)"', body)
            if c:
                out[("focus", "current", "")] = c.group(1)
                if re.search(r"paused=yes", body):
                    out[("focus", "paused", "")] = "yes"
        m = _IDEAS.search(text)
        for idea in m.group(1).split() if m else ():
            if idea in self.spirits:
                continue
            cat = self.ideas.get(idea, "?")
            if cat == "hidden_ideas":
                continue
            if cat in self.laws:
                out[("law", cat, "")] = idea
            else:
                out[("idea", cat, idea)] = idea
        m = _ADVISORS.search(text)
        for slot, cid in _APPOINTED.findall(m.group(1)) if m else ():
            token = self.character(int(cid))[0]
            out[("advisor", slot, token)] = token
        return out

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        todo = [t for t in (parse(ln, self.names, self.character) for ln in lines) if t]
        if day != self.day:
            self.days += 1
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
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
        for change, tag, (kind, slot, _), value, was in rows:
            self.f.write("\t".join((date, change, tag, kind, slot, value, was)) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
