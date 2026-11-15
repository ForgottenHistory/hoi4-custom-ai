"""What the recorders share, so that a step stays short at high game speed.

country(tag)      bridge.country TAG's response, read once per step: new_step() (cmdrec and battlerec call it at
                  each new date) starts a step; before the first new_step() nothing is kept.
part(tags, days)  the TAGS to read today when each is read once every EVERY days (7), so a weekly read of the ~85
                  producing countries is ~12 a day instead of 85 at once. Today is the count of new_step() days
                  (the same for every recorder, so they read the same countries and share the reads), else DAYS.
objects(cls)      the addresses of the objects of class CLS (as Process.find_objects), from a scan of the game's
                  memory on a thread: one pass for every class asked for so far, again SCAN_SECONDS after the last
                  ended. A scan takes seconds; only the first call for a class waits for one. The addresses may be
                  stale or miss objects made since the scan, so callers check what they find (as they did).
                  The scan compares every aligned 8-byte word of the game's private memory with the classes'
                  vtables (numpy), one pass for all of them: ~5 s for 8 GB.
want(*classes)    add CLASSES to the scan and start it, so a recorder made at startup has them by its first step.
ids(cls, handle)  {id: [address]} of the scanned CLS objects of bridge handle HANDLE (handle at +0x8, id at
                  +0xc); more than one address when stale copies are left in memory.
"""
import threading
import time

import numpy as np
from hoi4bridge import send

SCAN_SECONDS = 30

_step = None
_blocks = {}
_day, _days = None, None


def new_step(day=None):
    """A new step; DAY the in-game day (counted for part())."""
    global _step, _day, _days
    _step = (_step or 0) + 1
    _blocks.clear()
    if day is not None and day != _day:
        _day, _days = day, (_days or 0) + 1


def country(tag):
    if _step is not None and tag in _blocks:
        return _blocks[tag]
    r = send(f"bridge.country {tag}")
    if _step is not None:
        _blocks[tag] = r
    return r


def part(tags, days, every=7):
    today = _days if _days is not None else days
    return [t for i, t in enumerate(tags or ()) if i % every == today % every]


class _Scanner:
    def __init__(self):
        self.lock = threading.Condition()
        self.found = {}         # class -> [address] of the last scan that looked for it
        self.wanted = set()
        self.thread = None
        self.vtables = {}

    def _scan(self, proc, classes):
        for c in classes:
            if c not in self.vtables:
                self.vtables[c] = proc.vtable_of(c)
        vts = {self.vtables[c]: c for c in classes if self.vtables[c] is not None}
        out = {c: [] for c in classes}
        if not vts:
            return out
        want = np.array(sorted(vts), dtype="<u8")
        chunk = 1 << 24
        for base, size in proc.regions():        # regions are page aligned, so word i is at base + off + 8 i
            for off in range(0, size, chunk):
                b = proc.read(base + off, min(chunk, size - off))
                if not b:
                    continue
                words = np.frombuffer(b, dtype="<u8", count=len(b) // 8)
                hit = (words == want[0]) if len(want) == 1 else np.isin(words, want)
                for i in np.flatnonzero(hit):
                    out[vts[int(words[i])]].append(base + off + 8 * int(i))
        return out

    def _run(self):
        from hoi4ai.memory import Process
        proc = Process()
        while True:
            with self.lock:
                classes = sorted(self.wanted)
            try:
                found = self._scan(proc, classes)
            except Exception:       # the game closed, or a read failed: keep the last scan
                found = {}
            with self.lock:
                self.found.update(found)
                self.lock.notify_all()
                self.lock.wait_for(lambda: self.wanted - set(self.found), timeout=SCAN_SECONDS)

    def want(self, classes):
        with self.lock:
            if not set(classes) <= self.wanted:
                self.wanted.update(classes)
                self.lock.notify_all()
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="object scan", daemon=True)
                self.thread.start()

    def objects(self, cls):
        self.want((cls,))
        with self.lock:
            self.lock.wait_for(lambda: cls in self.found)
            return list(self.found[cls])


_scanner = _Scanner()


def objects(cls):
    return _scanner.objects(cls)


def want(*classes):
    """Add CLASSES to the scan and start it, before anyone asks (so the first scan finds them all in one pass)."""
    _scanner.want(classes)


_proc = None


def ids(cls, handle):
    """{id: [address]} of the scanned CLS objects whose bridge handle is HANDLE. Stale objects may be among them."""
    global _proc
    from hoi4ai.memory import Process
    if _proc is None:
        _proc = Process()
    out = {}
    for a in objects(cls):
        try:
            if _proc.i32(a + 8) == handle:
                out.setdefault(_proc.i32(a + 0xc), []).append(a)
        except OSError:
            pass
    return out
