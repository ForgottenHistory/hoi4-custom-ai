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
import os
import re
import time

from hoi4bridge import send

FULL = 100000
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


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


def front_ids():
    """Ids of every CFront the game holds (one bridge.snap: ids grow over a game with wide gaps, so stepping
    through them stopped long before the later fronts)."""
    path = os.path.join(CACHE, "fronts_snap.tsv")
    os.makedirs(CACHE, exist_ok=True)
    send("bridge.snap reset " + path)
    send("bridge.snap now 66 class=CFront i64")
    for attempt in range(20):  # the game may still hold the file for a moment
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            break
        except PermissionError:
            time.sleep(0.05)
    else:
        raise PermissionError(path)
    out = []
    for line in text.splitlines():
        p = line.split("	")
        if not line.startswith("#") and p[0].isdigit():
            out.append(int(p[0]))
    return sorted(set(out))


def _addresses(type_, ids, chunk=100):
    out = {}
    for k in range(0, len(ids), chunk):
        part = ids[k:k + chunk]
        for oid, r in zip(part, send(*[f"bridge.object {type_} {i}" for i in part])):
            m = re.search(r"@0x([0-9a-fA-F]+)", r)
            if m:
                out[oid] = int(m.group(1), 16)
    return out


def sections(proc, me, target, ctrl):
    """The sections of the CFronts between our land and the target's (the far side mostly the target's)."""
    out, seen = [], set()
    for i, a in sorted(_addresses(66, front_ids()).items()):
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


def calibrate(proc, army_id, front_id, division_ids, step=2000, width=4000, sel=2):
    """Where the game puts a front line for each stretch of a CFront: draw short test lines on ARMY_ID from 0% to
    100%, read back the border pairs each covers, delete them. (How the game measures the percent is its own
    business; this measures it.) SEL is the line's +0x50 (2 in every recorded line; it may pick a section).
    Returns {our border province: position 0..1}."""
    ds = " ".join(map(str, division_ids))
    seen = collections.defaultdict(list)
    for start in range(0, FULL, step):
        end = min(FULL, start + width)
        before = {lid for lid, _ in lines(proc, army_id)}
        if not send(f"bridge.plan front {army_id} {front_id} {start} {end} s={sel} {ds}")[0].startswith("OK"):
            continue
        new = [(lid, pairs) for lid, pairs in lines(proc, army_id) if lid not in before]
        for lid, pairs in new:
            for ours, _ in pairs:
                seen[ours].append((start + end) / 2 / FULL)
        if new:
            send(f"bridge.plan delete {army_id} " + " ".join(str(lid) for lid, _ in new))
    return {p: sum(v) / len(v) for p, v in seen.items()}


def make(m, armies, border, front_id, vps, ctrl, target, fast=None, near=4, reach=3, always=()):
    """armies: {army id: [divisions]}; border: {our border province: position 0..1} on CFront FRONT_ID (from
    calibrate); vps: {province: victory points} of the target. Returns [(army id, front id, start, end, target
    province, [division ids])]. The armies other than FAST share the border between them, in the order they stand
    (each up to halfway to the next); the FAST army gets its own stretch, where the middle half of its divisions
    stand. Each army's offensive line goes to the most valuable victory point within NEAR provinces across its
    stretch (else the nearest)."""
    pv = m.provinces

    def d2(p, xy):
        return (pv[p].x - xy[0]) ** 2 + (pv[p].y - xy[1]) ** 2

    # how far each province is from the border: an army mostly farther than REACH (a garrison elsewhere) gets none,
    # unless it is in ALWAYS (the army group's)
    away = {p: 0 for p in border}
    todo = collections.deque(border)
    while todo:
        p = todo.popleft()
        if away[p] < reach:
            for n in m.neighbours.get(p, {}):
                if n not in away and m.is_land(n):
                    away[n] = away[p] + 1
                    todo.append(n)
    pos = dict(border)  # every province of ours: the position of its nearest border province
    todo = collections.deque(border)
    while todo:
        p = todo.popleft()
        for n in m.neighbours.get(p, {}):
            if n not in pos and m.is_land(n) and ctrl[n] != target:
                pos[n] = pos[p]
                todo.append(n)
    where = {}
    for aid, ds in armies.items():
        if not ds or (aid not in always and sum(d.province in away for d in ds) < len(ds) / 2):
            continue
        at = sorted(pos[d.province] for d in ds if d.province in pos)
        if at:
            where[aid] = at
    stretches = {}
    line_armies = sorted((a for a in where if a != fast), key=lambda a: where[a][len(where[a]) // 2])
    lo_all, hi_all = min(border.values()), max(border.values())
    meds = [where[a][len(where[a]) // 2] for a in line_armies]
    for k, aid in enumerate(line_armies):
        lo = lo_all if k == 0 else (meds[k - 1] + meds[k]) / 2
        hi = hi_all if k == len(line_armies) - 1 else (meds[k] + meds[k + 1]) / 2
        stretches[aid] = (lo, hi)
    if fast in where:
        at = where[fast]
        lo, hi = at[len(at) // 4], at[3 * len(at) // 4]
        if hi - lo < 0.04:
            lo, hi = lo - 0.02, hi + 0.02
        stretches[fast] = (max(0.0, lo), min(1.0, hi))
    out = []
    for aid, (lo, hi) in stretches.items():
        stretch = [p for p, v in border.items() if lo <= v <= hi] or \
                  [min(border, key=lambda p: abs(border[p] - (lo + hi) / 2))]
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
        out.append((aid, front_id, int(lo * FULL), int(hi * FULL), tgt, [d.id for d in armies[aid]]))
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


def arrows(proc, army_id):
    """{front line id: how many offensive lines it has}. A front line's offensive lines are a vector at +0x1f8 of
    its COrderInstance (count 1 on every army with an arrow, 0 on one the game had dropped the arrow of)."""
    a = _addr(53, army_id)
    if not a:
        return {}
    rd = lambda addr, n: int.from_bytes(proc.read(addr, n), "little")
    out = {}
    try:
        data, n = rd(a + 0x98, 8), rd(a + 0xa4, 4)
        for k in range(n if data and n < 2000 else 0):
            o = rd(data + k * 8, 8)
            out[rd(o + 0x244, 4)] = rd(o + 0x1f8 + 12, 4)
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
    for aid, front, start, end, tgt, dids in plans:
        fid, sel = front if isinstance(front, tuple) else (front, 2)
        ds = " ".join(map(str, dids))
        before = {lid for lid, _ in lines(proc, aid)}
        r1 = send(f"bridge.plan front {aid} {fid} {start} {end} s={sel} {ds}")[0]
        new = [lid for lid, _ in lines(proc, aid) if lid not in before]
        if r1.startswith("OK") and new:
            r2 = send(f"bridge.plan arrow {aid} #{new[0]} {tgt} {ds}")[0]
        else:
            r2 = "-"
        out.append((aid, r1.startswith("OK"), f"army {aid}: front {fid} s={sel} {start / 1000:.0f}-{end / 1000:.0f}% "
                                               f"(line {new[0] if new else '?'}): {r1}; offensive to {tgt}: {r2}"))
    return out


def zones(m, specs, ctrl, ours):
    """{province: army id}: each of our provinces in the zone of the army whose stretch of the border is nearest.
    specs: [(army id, {border province: position}, lo, hi)]; the border moves in war, so every province of ours
    takes the position of its nearest measured border province (through our land)."""
    out = {}
    for border in {id(b): b for _, b, _, _ in specs}.values():
        pos = {p: v for p, v in border.items() if ctrl[p] in ours}
        todo = collections.deque(pos)
        while todo:
            p = todo.popleft()
            for n in m.neighbours.get(p, {}):
                if n not in pos and m.is_land(n) and ctrl[n] in ours:
                    pos[n] = pos[p]
                    todo.append(n)
        mine = [(aid, lo, hi) for aid, b, lo, hi in specs if b is border]
        for p, v in pos.items():
            for aid, lo, hi in mine:
                if lo <= v <= hi:
                    out.setdefault(p, aid)
    return out


IMMOBILE = 3  # cohesion: 0 flexible, 1 balanced, 2 rigid, 3 immobile


def cohesion(army_ids, value=IMMOBILE):
    return [f"army {a}: " + send(f"bridge.plan cohesion {a} {value}")[0] for a in army_ids]


def execute(army_ids):
    return [f"army {a}: " + send(f"bridge.plan execute {a}")[0] for a in army_ids]
