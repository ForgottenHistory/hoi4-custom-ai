"""Doctrines: every country's grand doctrine and subdoctrines, read from the game's memory.

DoctrineLog(path, names).step(date, lines) writes to doctrines.tsv:
  date  change  country  folder  track  doctrine  was  mastery
folder land / naval / air / special_forces; track empty for the folder's grand doctrine, else the track (infantry,
armor, ...) of a subdoctrine; doctrine the game's key (superior_firepower, defensive_postures), was the one it
replaced; mastery the track's mastery (subdoctrines only; written, not compared). change: start (at the first read),
new (picked where there was none), changed (replaced), removed. Tracks with no subdoctrine are left out.

Reads at the first call, each new in-game day and whenever the record LINES hold a doctrine command. The country
objects are found once (a memory scan, ~8 s) and again every RESCAN_DAYS days, for countries that appear later.

The player's commands (in command_record.tsv):
  CUnlockGrandDoctrineCommand@NDoctrines   +0x28 CGrandDoctrineTemplate*, +0x30 country id
  CUnlockSubDoctrineCommand@NDoctrines     +0x28 CFolderTemplate*, +0x30 CSubDoctrineTemplate*, +0x3c country id
Memory (all @NDoctrines): CCountryDoctrineStatus: country id +0x8, CFolderStatus array (stride 0x50, int count) at
+0x10/+0x18. CFolderStatus: CFolderTemplate* +0x8, CGrandDoctrineTemplate* +0x10, CTrackStatus array (stride 0x60)
+0x18 with count +0x20, owning CCountryDoctrineStatus* +0x30. CTrackStatus: CSubDoctrineTemplate* +0x8, mastery *
100000 +0x18. Templates: name std::string +0x28 (GRAND_DOCTRINE_..., SUBDOCTRINE_...); a folder's ui_doctrine_tab_X
at +0xe0. A track's index is its place in the grand doctrine's `tracks` list (common/doctrines/grand_doctrines).
"""
import os

from hoi4ai import pdx, shared

RESCAN_DAYS = 7
HEADER = "date\tchange\tcountry\tfolder\ttrack\tdoctrine\twas\tmastery"
CLASSES = ("CUnlockGrandDoctrineCommand@NDoctrines", "CUnlockSubDoctrineCommand@NDoctrines")
STATUS = "CCountryDoctrineStatus@NDoctrines"
FOLDER = "CFolderStatus@NDoctrines"


def game_doctrines(files=None):
    """{NAME: key} of every grand doctrine and subdoctrine, and {grand NAME: [track keys]}, from the game files."""
    from hoi4ai.gamefiles import GameFiles
    files = files or GameFiles()
    keys, tracks = {}, {}
    folders = ["common/doctrines/grand_doctrines"] + [
        "common/doctrines/subdoctrines/" + d for d in ("land", "sea", "air", "special_forces")]
    for folder in folders:
        for path in files.listdir(folder, ".txt").values():
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                for key, _, body in pdx.parse(f.read()):
                    if not isinstance(body, list):
                        continue
                    name = next((v for k, _, v in body if k == "name" and isinstance(v, str)), key.upper())
                    keys[name] = key
                    t = next((v for k, _, v in body if k == "tracks" and isinstance(v, list)), None)
                    if t is not None:
                        tracks[name] = [k for k, _, _ in t]
    return keys, tracks


class DoctrineLog:
    def __init__(self, path, names=None):
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        from hoi4ai.templates import _string
        self.proc = Process()
        self._string = _string
        self.names = names or Names()
        try:
            self.keys, self.tracks = game_doctrines()
        except OSError:
            self.keys, self.tracks = {}, {}
        self.status = {}            # country id -> CCountryDoctrineStatus address
        self.text = {}              # template address -> name
        self.last = None            # (country, folder, track) -> (doctrine, mastery)
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def _name(self, t, off=0x28):
        if t not in self.text:
            self.text[t] = self._string(self.proc, t + off) if t else ""
        return self.text[t]

    def find(self):
        """Every live CCountryDoctrineStatus by country id (a copy whose first folder doesn't point back is stale)."""
        p, out = self.proc, {}
        for a in shared.objects(STATUS):
            cid, fs, n = p.i32(a + 8), p.u64(a + 0x10), p.i32(a + 0x18)
            if 0 <= cid < 10000 and 0 < n <= 16 and p.class_of(fs) == FOLDER and p.u64(fs + 0x30) == a:
                out[cid] = a
        self.status = out

    def read(self):
        p, out = self.proc, {}
        for cid, a in self.status.items():
            if p.i32(a + 8) != cid:
                continue
            tag = self.names.tag(cid)
            fs, n = p.u64(a + 0x10), p.i32(a + 0x18)
            for k in range(max(0, min(n, 16))):
                f = fs + 0x50 * k
                folder = self._name(p.u64(f + 8), 0xe0).replace("ui_doctrine_tab_", "")
                gname = self._name(p.u64(f + 0x10))
                if not gname:
                    continue
                out[(tag, folder, "")] = (self.keys.get(gname, gname), "")
                names = self.tracks.get(gname, [])
                ts, m = p.u64(f + 0x18), p.i32(f + 0x20)
                for j in range(max(0, min(m, 16))):
                    sub = self._name(p.u64(ts + 0x60 * j + 8))
                    if sub:
                        track = names[j] if j < len(names) else str(j)
                        out[(tag, folder, track)] = (self.keys.get(sub, sub), f"{p.i32(ts + 0x60 * j + 0x18) / 100000:g}")
        return out

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        if self.last is not None and day == self.day and not any(c in ln for ln in lines for c in CLASSES):
            return 0
        if day != self.day:
            self.days += 1
        self.day = day
        if self.last is None or self.days % RESCAN_DAYS == 0:
            self.find()
        now = self.read()
        if self.last is None:
            rows = [("start", k, v[0], "", v[1]) for k, v in sorted(now.items())]
        else:
            rows = [("new" if k not in self.last else "changed", k, v[0], self.last.get(k, ("",))[0], v[1])
                    for k, v in sorted(now.items()) if self.last.get(k, (None,))[0] != v[0]]
            rows += [("removed", k, "", self.last[k][0], "") for k in sorted(self.last) if k not in now]
        self.last = now
        for change, (tag, folder, track), doc, was, mastery in rows:
            self.f.write("\t".join((date, change, tag, folder, track, doc, was, mastery)) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
