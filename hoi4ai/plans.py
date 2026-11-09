"""Battle plans for our armies, as a player draws them: a front line on a stretch of the border with the target and
an offensive line to a nearby victory point. Divisions on a plan build up the planning bonus, which plain move
orders never get. With the army's cohesion immobile the game doesn't move them along the plan, so our own orders
keep moving them while planning builds.

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


def along(m, secs, own):
    """{province: position along the border (0..1, the sections in turn)} for our provinces, each taking the
    position of its nearest border province (through our land)."""
    pos = {}
    for sec in secs:
        span = sec.end - sec.start
        for i, p in enumerate(sec.ours):
            pos.setdefault(p, sec.start + (i + 0.5) / len(sec.ours) * span)
    todo = collections.deque(pos)
    while todo:
        p = todo.popleft()
        for n in m.neighbours.get(p, {}):
            if n not in pos and n in own:
                pos[n] = pos[p]
                todo.append(n)
    return pos


def make(m, armies, secs, vps, ctrl, target, near=4, reach=3, always=()):
    """armies: {army id: [divisions]}; secs: [Section]; vps: {province: victory points} of the target.
    Returns [(army id, front id, start, end, target province, [division ids])]: each army's front line on the
    stretch of border where its divisions stand (the middle 80% of them, along the border); its offensive
    line to the most valuable victory point within NEAR provinces across its stretch (else the nearest)."""
    pv = m.provinces

    def d2(p, xy):
        return (pv[p].x - xy[0]) ** 2 + (pv[p].y - xy[1]) ** 2

    # how far each province is from the border: an army mostly farther than REACH (a garrison elsewhere) gets none,
    # unless it is in ALWAYS (the army group's)
    border = {p for sec in secs for p in sec.ours}
    away = {p: 0 for p in border}
    todo = collections.deque(border)
    while todo:
        p = todo.popleft()
        if away[p] < reach:
            for n in m.neighbours.get(p, {}):
                if n not in away and m.is_land(n):
                    away[n] = away[p] + 1
                    todo.append(n)
    pos = along(m, secs, {p for p in m.provinces if m.is_land(p)} - {p for p in m.provinces if ctrl[p] == target})
    out = []
    for aid, ds in armies.items():
        if not ds or (aid not in always and sum(d.province in away for d in ds) < len(ds) / 2):
            continue
        # the stretch where the army's divisions stand (the middle 80% of them), on the section most of them face
        at = sorted(pos[d.province] for d in ds if d.province in pos)
        if not at:
            continue
        sec = min(secs, key=lambda x: (not x.start <= at[len(at) // 2] <= x.end, abs((x.start + x.end) / 2 - at[len(at) // 2])))
        at = [min(max(v, sec.start), sec.end) for v in at]
        lo, hi = at[int(len(at) * 0.1)], at[min(len(at) - 1, int(len(at) * 0.9))]
        span = sec.end - sec.start
        i0 = int((lo - sec.start) / span * len(sec.ours))
        i1 = max(i0 + 1, min(len(sec.ours), int((hi - sec.start) / span * len(sec.ours)) + 1))
        lo, hi = sec.start + i0 / len(sec.ours) * span, sec.start + i1 / len(sec.ours) * span
        stretch = sec.ours[i0:i1]
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
        out.append((aid, sec.front, int(lo * FULL), int(hi * FULL), tgt, [d.id for d in ds]))
    return out


def lines(proc, army_id):
    """The army's lines as the game holds them: [(line id, [(our province, their province)..])]. An army's
    COrdersGroup lists its COrderInstances at +0x98; each has its id at +0x244 and its border pairs at +0x88."""
    a = _addr(53, army_id)
    if not a:
        return []

    def vec(addr):
        data = int.from_bytes(proc.read(addr, 8), "little")
        n = int.from_bytes(proc.read(addr + 12, 4), "little")
        return [int.from_bytes(proc.read(data + k * 8, 8), "little") for k in range(n)] if data and n < 2000 else []

    out = []
    try:
        for o in vec(a + 0x98):
            lid = int.from_bytes(proc.read(o + 0x244, 4), "little")
            provs = [int.from_bytes(proc.read(p + 0xa4, 4), "little") for p in vec(o + 0x88)]
            out.append((lid, list(zip(provs[0::2], provs[1::2]))))
    except (OSError, TypeError):
        pass
    return out


def clear(proc, army_ids):
    """Delete every line of these armies (the game AI's, or ours from before). Returns reply lines."""
    out = []
    for aid in army_ids:
        ids = [lid for lid, _ in lines(proc, aid)]
        if ids:
            out.append(f"army {aid}: " + send(f"bridge.plan delete {aid} " + " ".join(map(str, ids)))[0])
    return out


def draw(proc, plans):
    """Send the front lines, then each offensive line on its army's new front line. Returns [(army id, ok, line)]."""
    out = []
    for aid, fid, start, end, tgt, dids in plans:
        ds = " ".join(map(str, dids))
        before = {lid for lid, _ in lines(proc, aid)}
        r1 = send(f"bridge.plan front {aid} {fid} {start} {end} {ds}")[0]
        new = [lid for lid, _ in lines(proc, aid) if lid not in before]
        if r1.startswith("OK") and new:
            r2 = send(f"bridge.plan arrow {aid} #{new[0]} {tgt} {ds}")[0]
        else:
            r2 = "-"
        out.append((aid, r1.startswith("OK"), f"army {aid}: front {fid} {start / 1000:.0f}-{end / 1000:.0f}% "
                                               f"(line {new[0] if new else '?'}): {r1}; offensive to {tgt}: {r2}"))
    return out


IMMOBILE = 3  # cohesion: 0 flexible, 1 balanced, 2 rigid, 3 immobile


def cohesion(army_ids, value=IMMOBILE):
    return [f"army {a}: " + send(f"bridge.plan cohesion {a} {value}")[0] for a in army_ids]


def execute(army_ids):
    return [f"army {a}: " + send(f"bridge.plan execute {a}")[0] for a in army_ids]
