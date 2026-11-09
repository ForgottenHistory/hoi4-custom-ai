"""Draw the battle plans as the game holds them, next to where each army's divisions stand.

    python tools/planmap.py [--tag GER] [--target POL] [--out FILE] [--no-open]

Left: the border with the target (grey) and each army's front lines (our side of the border, in the army's colour,
numbered by army). Right: each army's divisions (province coloured by
the army with most divisions there, the number of them). Sends nothing.
"""
import argparse
import collections
import datetime
import os
import re

from PIL import Image, ImageDraw

import _bridge  # noqa: F401
from controller import country_ids, game_time
from heatmap import OTHER, OURS, FOE, SEA, font, panel
from hoi4ai import mapdata, plans
from hoi4ai.divisions import Snapshotter
from hoi4ai.live import Provinces
from hoi4ai.memory import Process

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLOURS = [(230, 25, 75), (60, 140, 60), (0, 110, 200), (245, 130, 48), (145, 30, 180), (70, 190, 190),
           (240, 50, 230), (128, 100, 0), (0, 0, 128), (170, 110, 40)]


def leader_names():
    """{army id: general's name}, from the army's leader pointer (+0x88) and the leader's name (+0x40)."""
    pr, out = Process(), {}
    for aid in range(1, 40):
        a = plans._addr(53, aid)
        if not a:
            continue
        try:
            leader = int.from_bytes(pr.read(a + 0x88, 8), "little")
            if leader and pr.class_of(leader) == "CArmyLeader":
                raw = pr.read(leader + 0x40, 32)
                ptr = int.from_bytes(raw[:8], "little")
                length = int.from_bytes(raw[16:24], "little")
                txt = raw[:length] if length < 16 else pr.read(ptr, length)
                out[aid] = txt.decode("utf-8", "replace")
        except (OSError, TypeError, AttributeError):
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="GER")
    ap.add_argument("--target", default="POL")
    ap.add_argument("--out")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    tag, target = a.tag.upper(), a.target.upper()

    m = mapdata.load()
    ids = mapdata.province_image(m)
    tags, cid = country_ids()
    me, tgt = cid[tag], cid[target]
    ctrl = Provinces().refresh().controller
    divs = [d for d in Snapshotter(os.path.join(REPO, ".cache", "planmap_snap.tsv")).take() if d.owner == tag]
    secs = plans.sections(Process(), me, tgt, ctrl)
    vps = {p: v for st in m.states.values() for p, v in st.victory_points.items() if ctrl[p] == tgt}
    army_ids = sorted({d.army for d in divs if d.army})
    armies = {aid: [d for d in divs if d.army == aid] for aid in army_ids}
    names = leader_names()
    colour = {aid: COLOURS[i % len(COLOURS)] for i, aid in enumerate(army_ids)}

    # each army's lines as the game holds them
    proc = Process()
    held = {aid: plans.lines(proc, aid) for aid in army_ids}
    stretch = {aid: [o for _, pairs in ls for o, _ in pairs] for aid, ls in held.items()}

    focus = {p for sec in secs for p in sec.ours} | {p for p in m.provinces if m.is_land(p) and ctrl[p] == tgt}
    focus |= {d.province for d in divs if any(d.province in m.neighbours.get(q, {}) or d.province == q
                                              for q in [p for sec in secs for p in sec.ours][:0])}
    xs = [m.provinces[p].x for p in focus]
    ys = [m.provinces[p].y for p in focus]
    pad = 60
    x0, x1 = max(0, int(min(xs)) - pad), min(m.width, int(max(xs)) + pad)
    y0, y1 = max(0, int(min(ys)) - pad), min(m.height, int(max(ys)) + pad)
    scale = max(1, min(4, 900 // max(1, x1 - x0)))

    def base(p):
        pr = m.provinces.get(p)
        if pr is None or pr.kind != "land":
            return SEA
        c = ctrl[p]
        return OURS if c == me else FOE if c == tgt else OTHER

    def light(c, f=0.45):
        return tuple(int(v + (255 - v) * f) for v in c)

    col1 = {p: (200, 200, 200) for sec in secs for p in sec.ours}
    for aid, ps in stretch.items():
        col1.update({p: colour[aid] for p in ps})
    img1 = panel(ids, (y0, y1, x0, x1), scale, col1, base)

    here = collections.defaultdict(collections.Counter)
    for d in divs:
        here[d.province][d.army] += 1
    col2 = {p: light(colour.get(c.most_common(1)[0][0], (90, 90, 90)), 0.2) for p, c in here.items()}
    img2 = panel(ids, (y0, y1, x0, x1), scale, col2, base)

    def xy(p):
        pr = m.provinces[p]
        return (pr.x - x0) * scale, (pr.y - y0) * scale

    f_small, f_big = font(9 + 2 * scale), font(14 + 2 * scale)
    d1, d2 = ImageDraw.Draw(img1), ImageDraw.Draw(img2)
    for p, v in vps.items():
        x, y = xy(p)
        r = (2 + min(6, v / 4)) * scale
        for d in (d1, d2):
            d.regular_polygon((x, y - 8 * scale, r), 5, fill=(255, 215, 0), outline=(0, 0, 0))
    for aid, ps in stretch.items():  # the army's number on its lines
        for p in ps:
            d1.text(xy(p), str(aid), fill=(255, 255, 255), font=f_small, anchor="mm")
    for p, c in here.items():
        top = c.most_common(1)[0][0]
        d2.text(xy(p), str(sum(c.values())), fill=(255, 255, 255) if top in colour else (0, 0, 0), font=f_small,
                anchor="mm")

    head, legend = 70, 26 * (len(army_ids) + 1)
    W, H = img1.width + img2.width + 30, img1.height + head + legend + 10
    out = Image.new("RGB", (W, H), (250, 250, 248))
    out.paste(img1, (10, head))
    out.paste(img2, (img1.width + 20, head))
    d = ImageDraw.Draw(out)
    now = game_time(tag)
    d.text((10, 8), f"{tag} battle plans vs {target}, {now:%d %b %Y %H:00}", fill=(0, 0, 0), font=f_big)
    d.text((10, 40), "Front lines as the game holds them (our side, numbered by army); grey: the border with the "
                     "target", fill=(60, 60, 60), font=f_small)
    d.text((img1.width + 20, 40), "Where each army's divisions stand (colour = army with most there)",
           fill=(60, 60, 60), font=f_small)
    yy = head + img1.height + 8
    for aid in army_ids:
        d.rectangle((10, yy, 30, yy + 18), fill=colour[aid])
        ls = ", ".join(f"line {lid}: {len(pairs)} border pairs" for lid, pairs in held[aid]) or "no lines"
        d.text((40, yy), f"army {aid} ({names.get(aid, '?')}), {len(armies[aid])} divisions: {ls}",
               fill=(0, 0, 0), font=f_small)
        yy += 26
    path = a.out or os.path.join(REPO, "recordings", "heatmaps", f"plans_{tag}_{target}_{now:%Y%m%d_%H}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    print(path)
    if not a.no_open:
        os.startfile(path)


if __name__ == "__main__":
    main()
