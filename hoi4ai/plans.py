"""Battle plans for the infantry armies, as a player draws them: a front line on a stretch of the border with the
target and an offensive line to a nearby victory point, executed when the war starts. Divisions on a plan build
up the planning bonus before they attack, which plain move orders never get.

The border comes from the game's CFront objects (handle type 66): one per border between one of our areas and an
enemy area in a theatre. +0x58 lists its CFrontSections, connected stretches of border: each has our provinces in
order at +0x30 and (ours, theirs) province pairs at +0x48 ({data, cap, count} of CProvince*). A front line covers a
stretch of the CFront given in thousandths of a percent, running along the sections in turn (bridge.plan front;
a player's front line at Köslin was 80-100%, the end of the mainland section).
"""
import collections
import math
import re

from hoi4bridge import send

FULL = 100000


def _addr(type_, oid):
    m = re.search(r"@0x([0-9a-fA-F]+)", send(f"bridge.object {type_} {oid}")[0])
    return int(m.group(1), 16) if m else 0


class Section:
    """A connected stretch of a CFront: our provinces in order, the enemy provinces across, and where it lies on
    the CFront (fractions)."""

    def __init__(self, front, ours, theirs, start, end):
        self.front, self.ours, self.theirs, self.start, self.end = front, ours, theirs, start, end


def _provinces(proc, vec):
    data = int.from_bytes(proc.read(vec, 8), "little")
    n = int.from_bytes(proc.read(vec + 12, 4), "little")
    if not data or not 0 < n < 5000:
        return []
    raw = proc.read(data, n * 8)
    out = []
    for k in range(n):
        p = int.from_bytes(raw[k * 8:k * 8 + 8], "little")
        if proc.class_of(p) != "CProvince":
            return []
        out.append(int.from_bytes(proc.read(p + 0xa4, 4), "little"))
    return out


def sections(proc, me, target, ctrl, max_misses=40):
    """The sections of the CFronts between our land and the target's (the far side mostly the target's)."""
    out, seen, misses, i = [], set(), 0, 0
    while misses < max_misses:
        i += 1
        a = _addr(66, i)
        if not a:
            misses += 1
            continue
        misses = 0
        try:
            data = int.from_bytes(proc.read(a + 0x58, 8), "little")
            n = int.from_bytes(proc.read(a + 0x64, 4), "little")
            if not data or not 0 < n < 100:
                continue
            secs = []
            for k in range(n):
                s = int.from_bytes(proc.read(data + k * 8, 8), "little")
                pairs = _provinces(proc, s + 0x48)
                secs.append((_provinces(proc, s + 0x30), pairs[1::2]))
        except (OSError, TypeError):
            continue
        total = sum(len(o) for o, _ in secs)
        if not total:
            continue
        at = 0
        for ours, theirs in secs:
            frac0, at = at / total, at + len(ours)
            key = tuple(ours)
            if not ours or key in seen:
                continue
            if sum(ctrl[p] == me for p in ours) < 0.5 * len(ours):
                continue
            if not theirs or sum(ctrl[q] == target for q in theirs) < 0.5 * len(theirs):
                continue
            seen.add(key)
            out.append(Section(i, ours, sorted(set(theirs)), frac0, at / total))
    return out


def make(m, armies, secs, vps, ctrl, target, near=4):
    """armies: {army id: [divisions]}; secs: [Section]; vps: {province: victory points} of the target.
    Returns [(army id, front id, start, end, target province, [division ids])]: each army on the section nearest
    to it, armies along a section in the order they stand, each with a stretch as long as its share; its offensive
    line to the most valuable victory point within NEAR provinces across its stretch (else the nearest)."""
    pv = m.provinces

    def d2(p, xy):
        return (pv[p].x - xy[0]) ** 2 + (pv[p].y - xy[1]) ** 2

    on = collections.defaultdict(list)
    for aid, ds in armies.items():
        if not ds:
            continue
        xy = (sum(pv[d.province].x for d in ds) / len(ds), sum(pv[d.province].y for d in ds) / len(ds))
        k = min(range(len(secs)), key=lambda j: min(d2(p, xy) for p in secs[j].ours))
        on[k].append((min(range(len(secs[k].ours)), key=lambda j: d2(secs[k].ours[j], xy)), aid))
    out = []
    for k, lst in on.items():
        sec = secs[k]
        lst.sort()
        total = sum(len(armies[a]) for _, a in lst)
        at = 0.0
        for _, aid in lst:
            a0, at = at, at + len(armies[aid]) / total
            stretch = sec.ours[int(a0 * len(sec.ours)):max(int(a0 * len(sec.ours)) + 1, int(at * len(sec.ours)))]
            across = {q for p in stretch for q in m.neighbours.get(p, {}) if ctrl[q] == target}
            dist = {q: 1 for q in across}
            todo = collections.deque(across)
            while todo:  # through the target's land only
                p = todo.popleft()
                if dist[p] >= near:
                    continue
                for n in m.neighbours.get(p, {}):
                    if n not in dist and ctrl[n] == target:
                        dist[n] = dist[p] + 1
                        todo.append(n)
            cands = [q for q in vps if q in dist]
            if cands:
                tgt = max(cands, key=lambda q: (vps[q] / (1 + dist[q]), -dist[q]))
            else:
                mid = stretch[len(stretch) // 2]
                tgt = min(vps, key=lambda q: d2(q, (pv[mid].x, pv[mid].y)))
            span = sec.end - sec.start
            out.append((aid, sec.front, int((sec.start + a0 * span) * FULL), int((sec.start + at * span) * FULL),
                        tgt, [d.id for d in armies[aid]]))
    return out


def draw(plans):
    """Send the front lines and offensive lines. Returns [(army id, ok, reply line)]."""
    out = []
    for aid, fid, start, end, tgt, dids in plans:
        ds = " ".join(map(str, dids))
        r1 = send(f"bridge.plan front {aid} {fid} {start} {end} {ds}")[0]
        r2 = send(f"bridge.plan arrow {aid} {tgt} {ds}")[0] if r1.startswith("OK") else "-"
        out.append((aid, r1.startswith("OK"),
                    f"army {aid}: front {fid} {start / 1000:.0f}-{end / 1000:.0f}%: {r1}; offensive to {tgt}: {r2}"))
    return out


def delete(army_ids):
    return [f"army {a}: " + send(f"bridge.plan delete {a}")[0] for a in army_ids]


def execute(army_ids):
    return [f"army {a}: " + send(f"bridge.plan execute {a}")[0] for a in army_ids]
