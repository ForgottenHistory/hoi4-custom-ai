"""Battle plans for the infantry armies, as a player draws them: a front line on a stretch of the border with the
target and an offensive line to a nearby victory point, executed when the war starts. Divisions on a plan build
up the planning bonus before they attack, which plain move orders never get.

The border comes from the game's CFront objects (handle type 66): one per border between one of our areas and an
enemy area in a theatre. +0x20 lists our border provinces in order ({data, cap, count} of CProvince*); a front line
covers a stretch of that list, given in thousandths of a percent (bridge.plan front).
"""
import collections
import math
import re

from hoi4bridge import send

FULL = 100000


def _addr(type_, oid):
    m = re.search(r"@0x([0-9a-fA-F]+)", send(f"bridge.object {type_} {oid}")[0])
    return int(m.group(1), 16) if m else 0


def fronts(proc, me, target, ctrl, m, max_misses=40):
    """{front id: [our border provinces in order]} for the CFronts between our land and the target's."""
    out, misses, i = {}, 0, 0
    while misses < max_misses:
        i += 1
        a = _addr(66, i)
        if not a:
            misses += 1
            continue
        misses = 0
        try:
            data = int.from_bytes(proc.read(a + 0x20, 8), "little")
            n = int.from_bytes(proc.read(a + 0x2c, 4), "little")
            if not data or not 0 < n < 2000:
                continue
            raw = proc.read(data, n * 8)
            provs = []
            for k in range(n):
                p = int.from_bytes(raw[k * 8:k * 8 + 8], "little")
                if proc.class_of(p) != "CProvince":
                    break
                provs.append(int.from_bytes(proc.read(p + 0xa4, 4), "little"))
        except OSError:
            continue
        if provs and sum(ctrl[p] == me for p in provs) >= 0.5 * len(provs) and any(
                ctrl[q] == target for p in provs for q in m.neighbours.get(p, {})):
            out[i] = provs
    # the game keeps smaller fronts inside bigger ones (and copies); keep the biggest
    keep = {}
    for i in sorted(out, key=lambda f: -len(out[f])):
        if not any(set(out[i]) <= set(out[j]) for j in keep):
            keep[i] = out[i]
    return keep


def make(m, armies, border, vps):
    """armies: {army id: [divisions]}; border: {front id: [provinces]}; vps: {province: victory points} of the
    target. Returns [(army id, front id, start, end, target province, [division ids])]: each army on the border
    nearest to it, armies along a border in the order they stand, each with a stretch as long as its share."""
    pv = m.provinces

    def centre(ds):
        return (sum(pv[d.province].x for d in ds) / len(ds), sum(pv[d.province].y for d in ds) / len(ds))

    def near(xy, provs):
        return min(range(len(provs)), key=lambda k: (pv[provs[k]].x - xy[0]) ** 2 + (pv[provs[k]].y - xy[1]) ** 2)

    on = collections.defaultdict(list)
    for aid, ds in armies.items():
        if not ds:
            continue
        xy = centre(ds)
        fid = min(border, key=lambda f: min((pv[p].x - xy[0]) ** 2 + (pv[p].y - xy[1]) ** 2 for p in border[f]))
        on[fid].append((near(xy, border[fid]), aid))
    out = []
    for fid, lst in on.items():
        provs = border[fid]
        lst.sort()
        total = sum(len(armies[a]) for _, a in lst)
        at = 0.0
        for _, aid in lst:
            share = len(armies[aid]) / total
            start, end = at, at + share
            at = end
            mid = provs[min(len(provs) - 1, int((start + end) / 2 * len(provs)))]
            # the offensive line: the most valuable victory point for its distance from the stretch
            tgt = min(vps, key=lambda q: math.hypot(pv[q].x - pv[mid].x, pv[q].y - pv[mid].y) / (1 + vps[q] / 10.0))
            out.append((aid, fid, int(start * FULL), int(end * FULL), tgt, [d.id for d in armies[aid]]))
    return out


def draw(plans):
    """Send the front lines and offensive lines. Returns reply lines."""
    lines = []
    for aid, fid, start, end, tgt, dids in plans:
        ds = " ".join(map(str, dids))
        r1 = send(f"bridge.plan front {aid} {fid} {start} {end} {ds}")[0]
        r2 = send(f"bridge.plan arrow {aid} {tgt} {ds}")[0]
        lines.append(f"army {aid}: front {fid} {start / 1000:.0f}-{end / 1000:.0f}%: {r1}; offensive to {tgt}: {r2}")
    return lines


def execute(army_ids):
    return [f"army {a}: " + send(f"bridge.plan execute {a}")[0] for a in army_ids]
