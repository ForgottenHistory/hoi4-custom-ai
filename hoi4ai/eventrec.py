"""Event choices from a bridge.cmdrec record: which country answered which event, and with which option.

Every event a country answers (player click or AI pick, news events included) runs a CSelectEventOptionCommand:
  +0x2c  handle { type 50, event id }        id = namespace number * 100000 + event number
  +0x34  country id the event comes from (FROM: for a news event, the country it is about; else the receiver)
  +0x38  the event target: +0x40 country id that receives the event and answers it (0 when ROOT is not a country,
         e.g. an operative: then +0x34 answers), +0x44 a random seed Execute rewrites, +0x50.. CEventScope pointers
  +0xe8  option index (into the event's options), past the 0xa0 bytes the record keeps as hex: install_rules() asks
         the bridge for it ("F+0xe8=N" in the last column; needs a bridge with `cmdrec field`)
Events that fire without options to choose from (hidden events) run no command and aren't here.

In the game's memory a CEvent (bridge.object 50 ID) has its name as a std::string at +0x20 and its options at
+0x368 (pointer array, count +0x370); a CEventOption has its name ("AAT_norway.9.a") at +0x178.

EventLog(record, out).step() appends the choices recorded since the last call to events.tsv; cmdrec.py calls it
after each date mark. Each row's date is the in-game hour of its command (hoi4ai.clock: hourly ticks
counted from the marks). tools/events.py decodes a whole record afterwards.
"""
import os
import re

from hoi4ai.clock import Clock

from hoi4bridge import BridgeError, send

CLASS = "CSelectEventOptionCommand"
OPTION_OFF = 0xe8
HEADER = "date\tseq\tcountry\tfrom\tevent_id\tevent\toption\toption_name\toptions"


def install_rules():
    """Ask bridge.cmdrec for the option index. False if the bridge is too old to read past the recorded bytes."""
    send(f"bridge.cmdrec rules {CLASS} off")
    r = send(f"bridge.cmdrec field {CLASS} {OPTION_OFF:#x}")[0]
    return r.startswith("OK")


def _i32(b, off):
    return int.from_bytes(b[off:off + 4], "little", signed=True)


def parse(line):
    """A record line -> (seq, event id, from id, country id, option or None), or None for other commands."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 9 or p[2] != CLASS:
        return None
    b = bytes.fromhex(p[7])
    if len(b) < 0x44 or _i32(b, 0x2c) != 50:
        return None
    m = re.search(rf"\bF\+{OPTION_OFF:#x}=(-?\d+)", p[8])
    frm, country = _i32(b, 0x34), _i32(b, 0x40)
    return int(p[0]), _i32(b, 0x30), frm, country if country > 0 else frm, int(m.group(1)) if m else None


class Names:
    """Country tags and event / option names from the running game, cached."""

    def __init__(self):
        self.tags, self.events, self.proc = {}, {}, None

    def tag(self, cid):
        """The tag of country id CID. A miss asks for the 64 ids around it in one batched send."""
        if cid not in self.tags:
            ids = [cid] if not isinstance(cid, int) or cid < 0 else \
                [i for i in range(cid - cid % 64, cid - cid % 64 + 64) if i not in self.tags]
            try:
                for i, r in zip(ids, send(*[f"bridge.tag {i}" for i in ids])):
                    self.tags[i] = r.split("=", 1)[1].strip() if r.startswith("OK") and "=" in r else str(i)
            except BridgeError:
                pass
            self.tags.setdefault(cid, str(cid))
        return self.tags[cid]

    def event(self, eid):
        """(event name, [option names])."""
        if eid not in self.events:
            self.events[eid] = ("", [])
            try:
                r = send(f"bridge.object 50 {eid}")[0]
                m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK") else None
                if m:
                    self.events[eid] = self._read(int(m.group(1), 16))
            except (BridgeError, OSError):
                pass
        return self.events[eid]

    def _read(self, a):
        from hoi4ai.memory import Process
        from hoi4ai.templates import _string
        if self.proc is None:
            self.proc = Process()
        p = self.proc
        n = p.i32(a + 0x370)
        arr = p.u64(a + 0x368)
        opts = []
        if arr and 0 < n < 64:
            for i in range(n):
                o = p.u64(arr + 8 * i)
                opts.append(_string(p, o + 0x178) if o else "")
        return _string(p, a + 0x20), opts


class EventLog:
    def __init__(self, record, out, names=None):
        self.record, self.pos, self.date, self.lines = record, 0, "", []
        self.clock = Clock()
        self.names = names or Names()
        new = not os.path.exists(out)
        self.f = open(out, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def step(self):
        """Decode the complete lines added to the record since the last call (kept in .lines for other logs, such as
        templaterec's); returns the rows written."""
        self.lines = []
        try:
            with open(self.record, "rb") as g:
                g.seek(self.pos)
                data = g.read()
        except OSError:
            return 0
        end = data.rfind(b"\n") + 1
        self.pos += end
        n = 0
        self.lines = data[:end].decode("utf-8", errors="replace").splitlines()
        for line in self.lines:
            self.date = self.clock.feed(line)
            if line.startswith("#mark\t"):
                continue
            r = parse(line)
            if not r:
                continue
            seq, eid, frm, country, opt = r
            name, opts = self.names.event(eid)
            oname = opts[opt] if opt is not None and 0 <= opt < len(opts) else ""
            self.f.write("\t".join([self.date, str(seq), self.names.tag(country), self.names.tag(frm), str(eid), name,
                                    "" if opt is None else str(opt), oname, str(len(opts)) if opts else ""]) + "\n")
            n += 1
        self.f.flush()
        return n

    def close(self):
        self.f.close()
