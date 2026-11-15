"""Officer corps spirits: the army, navy and air spirits each country has picked.

SpiritLog(path, names, tags, others).step(date, lines) writes to spirits.tsv:
  date  change  country  branch  slot  spirit  was
branch army / navy / air; slot the idea category (academy_spirit, army_spirit, division_command_spirit,
naval_academy_spirit, ...); spirit the idea key (bold_attack_spirit), was the one it replaced (a slot with several
holds them comma-separated). change: start (the first read of a country), new (a slot filled), changed, removed.

Spirits are ideas: they show up in `ideas={ ... }` of a country's save-format block. The slots and which idea belongs
to which come from the game files (common/idea_tags: categories of type army_spirit / navy_spirit / air_spirit;
common/ideas: the ideas in each). Reads at the first call and each new in-game day: TAGS (the recorded countries) each
time, OTHERS() (a callable, e.g. MioLog's producing countries) every FULL_DAYS days, and the country of every
CAddIdeaCommand and CReplaceIdeaCommand in the record LINES at once.

The player's command (in command_record.tsv):
  CAddIdeaCommand       country id +0x28, CIdea* +0x30 (name std::string at +0x18 of the CIdea): filling an empty
                        slot; also used for other ideas (laws, ...)
  CReplaceIdeaCommand   country id +0x28, old CIdea* +0x30, new CIdea* +0x38: replacing a spirit
"""
import os
import re

from hoi4ai import pdx
from hoi4ai import shared
from hoi4bridge import send

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tbranch\tslot\tspirit\twas"
COMMANDS = ("CAddIdeaCommand", "CReplaceIdeaCommand")
_IDEAS = re.compile(r"\n\t*ideas=\{([^}]*)\}")


def game_spirits(files=None):
    """{idea key: (branch, slot)} of every officer corps spirit, from the game files."""
    from hoi4ai.gamefiles import GameFiles
    files = files or GameFiles()
    slots = {}
    for path in files.listdir("common/idea_tags", ".txt").values():
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for _, _, body in pdx.parse(f.read()):
                for cat, _, v in body if isinstance(body, list) else ():
                    t = next((x for k, _, x in v if k == "type"), "") if isinstance(v, list) else ""
                    if isinstance(t, str) and t.endswith("_spirit") and t != "national_spirit":
                        slots[cat] = t[:-len("_spirit")]
    out = {}
    for path in files.listdir("common/ideas", ".txt").values():
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for top, _, body in pdx.parse(f.read()):
                if top != "ideas" or not isinstance(body, list):
                    continue
                for cat, _, ideas in body:
                    if cat in slots and isinstance(ideas, list):
                        for key, _, v in ideas:
                            if isinstance(v, list):
                                out[key] = (slots[cat], cat)
    return out


def parse(line, names):
    """A record line -> the tag of the country an idea command is for, or None."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] not in COMMANDS:
        return None
    b = bytes.fromhex(p[7])
    return names.tag(int.from_bytes(b[0x28:0x2c], "little", signed=True)) if len(b) >= 0x30 else None


class SpiritLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.spirits = game_spirits()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(branch, slot): "spirit,..."}
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def read(self, tag):
        r = shared.country(tag)
        m = _IDEAS.search("\n".join(r)) if r and r[0].startswith("OK") else None
        out = {}
        for idea in sorted(m.group(1).split() if m else ()):
            if idea in self.spirits:
                k = self.spirits[idea]
                out[k] = f"{out[k]},{idea}" if k in out else idea
        return out

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
            if last is None:
                rows += [("start", tag, k, v, "") for k, v in sorted(now.items())]
            else:
                rows += [("new" if k not in last else "changed", tag, k, v, last.get(k, ""))
                         for k, v in sorted(now.items()) if last.get(k) != v]
                rows += [("removed", tag, k, "", last[k]) for k in sorted(last) if k not in now]
            self.held[tag] = now
        self.started = True
        for change, tag, (branch, slot), spirit, was in rows:
            self.f.write("\t".join((date, change, tag, branch, slot, spirit, was)) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
