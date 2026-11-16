"""The in-game hour of each line of a bridge.cmdrec record.

The game runs one CHourlyTickCommand per in-game hour, and the record holds every one, so counting them dates each
command exactly; the "#mark date=" lines (cmdrec, battlerec, the controller) anchor the count. A mark's date is read
before the mark is written, so it can be late by some ticks when the recorder is busy (seen: mostly 1, up to 32 at
speed 5), never early: the anchor is the latest (mark date - ticks before it). A mark more than REWIND hours behind
that anchor is a reload of an older save (the game was put back; a late mark is never that far behind): the clock
follows it, so the dates after the load are the replay's, not the abandoned timeline's.

    clock = Clock()
    for line in record:
        date = clock.feed(line)     # LINE's date, in the format of the marks ("13:00, 24 April, 1936" or
                                    # "1936-04-24 13:00"); "" before the first mark

The game's own text runs hours 1 to 24 (24:00 the day's end); written so for the first format.
"""
import datetime
import re

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
_GAME = re.compile(r"(\d+):(\d+), (\d+) (\w+), (\d+)")
_ISO = re.compile(r"(\d+)-(\d+)-(\d+) (\d+):(\d+)")
HOUR = datetime.timedelta(hours=1)
REWIND = 72 * HOUR


def parse(text):
    """(datetime, style) of a mark's date text, or (None, None)."""
    m = _GAME.match(text)
    if m and m.group(4) in MONTHS:
        d = datetime.datetime(int(m.group(5)), MONTHS.index(m.group(4)) + 1, int(m.group(3)))
        return d + int(m.group(1)) * HOUR, "game"
    m = _ISO.match(text)
    if m:
        return datetime.datetime(*map(int, m.groups())), "iso"
    return None, None


def fmt(d, style):
    if style == "iso":
        return f"{d:%Y-%m-%d %H:00}"
    if d.hour == 0:
        d -= HOUR
        return f"24:00, {d.day} {MONTHS[d.month - 1]}, {d.year}"
    return f"{d.hour}:00, {d.day} {MONTHS[d.month - 1]}, {d.year}"


class Clock:
    def __init__(self):
        self.base, self.style, self.ticks, self.date = None, None, 0, ""

    def feed(self, line):
        if line.startswith("#mark\t"):
            p = line.rstrip("\r\n").split("\t")
            if len(p) > 2 and p[2].startswith("date="):
                d, style = parse(p[2][5:].strip())
                if d is not None:
                    base = d - self.ticks * HOUR
                    if self.base is None or base > self.base or style != self.style or base < self.base - REWIND:
                        self.base, self.style = base, style
                    self._set()
        elif "\tCHourlyTickCommand\t" in line:
            self.ticks += 1
            self._set()
        return self.date

    def _set(self):
        if self.base is not None:
            self.date = fmt(self.base + self.ticks * HOUR, self.style)
