"""Draw what the planner sees: valuations and forces on the map, as a PNG (outside the game).

    python tools/heatmap.py [--tag GER] [--target POL] [--out FILE] [--no-open]

Runs one planning step on the live game (sends nothing) and draws two panels around the front:
  valuations: our front provinces by breakthrough score (yellow -> red), enemy provinces by attack score
              (light -> dark blue); chosen breakthrough points ringed, the enemy's top victory point starred.
  forces:     our divisions per province (green, with the count), enemy strength (red, organisation x strength).
"""
import argparse
import datetime
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import _bridge  # noqa: F401
from controller import country_ids, enemies_of, friends_of, game_time
from hoi4ai import mapdata
from hoi4ai.divisions import Snapshotter
from hoi4ai.front import Planner, power
from hoi4ai.live import Provinces

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEA, OTHER, OURS, FOE, FRIEND = (172, 198, 222), (222, 219, 210), (236, 232, 220), (240, 222, 222), (228, 232, 222)
HOT = [(255, 245, 170), (253, 174, 60), (215, 48, 31), (120, 0, 30)]
COLD = [(222, 235, 247), (130, 180, 215), (33, 102, 172), (8, 40, 100)]
GREEN = [(220, 240, 210), (120, 195, 110), (20, 120, 50), (0, 60, 25)]
RED = [(250, 220, 210), (240, 130, 110), (190, 30, 30), (100, 0, 10)]


def ramp(stops, x):
    x = min(max(x, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(x), len(stops) - 2)
    f = x - i
    return tuple(int(a + (b - a) * f) for a, b in zip(stops[i], stops[i + 1]))


def ranks(values):
    """{key: 0..1} by rank, so a few large values don't wash out the rest."""
    order = sorted(values, key=values.get)
    n = max(1, len(order) - 1)
    return {k: i / n for i, k in enumerate(order)}


def font(size):
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name), size)
        except OSError:
            pass
    return ImageFont.load_default()


def panel(ids, crop, scale, colours, base):
    """Colour each province pixel by colours.get(id, base(id)), draw province and controller borders."""
    y0, y1, x0, x1 = crop
    sub = ids[y0:y1, x0:x1]
    uniq, inv = np.unique(sub, return_inverse=True)
    lut = np.array([colours.get(int(u), base(int(u))) for u in uniq], np.uint8)
    rgb = lut[inv.reshape(sub.shape)]
    edge = np.zeros(sub.shape, bool)
    edge[:, 1:] |= sub[:, 1:] != sub[:, :-1]
    edge[1:, :] |= sub[1:, :] != sub[:-1, :]
    rgb[edge] = (rgb[edge] * 0.72).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB")
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


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
    lp = Provinces().refresh()
    ctrl = lp.controller
    divs = Snapshotter(os.path.join(REPO, ".cache", "heatmap_snap.tsv")).take()
    enemies = [cid[t] for t in enemies_of(tag) if t in cid]
    friends = [cid[t] for t in friends_of(tag) if t in cid]
    now = game_time(tag)
    pl = Planner(m, me)
    orders, rep = pl.plan(ctrl, divs, tag, enemies, tgt, tgt in enemies, friends)
    v = pl.view
    hostile = set(enemies) | {tgt}
    ours = {me} | set(friends)

    # crop around the front and the target country
    focus = set(v["front"]) | {p for p in m.provinces if m.is_land(p) and ctrl[p] == tgt}
    xs = [m.provinces[p].x for p in focus]
    ys = [m.provinces[p].y for p in focus]
    pad = 40
    x0, x1 = max(0, int(min(xs)) - pad), min(m.width, int(max(xs)) + pad)
    y0, y1 = max(0, int(min(ys)) - pad), min(m.height, int(max(ys)) + pad)
    scale = max(1, min(4, 900 // max(1, x1 - x0)))

    def base(p):
        pr = m.provinces.get(p)
        if pr is None or pr.kind != "land":
            return SEA
        c = ctrl[p]
        return OURS if c == me else FRIEND if c in ours else FOE if c in hostile else OTHER

    # panel 1: valuations
    bt, at = v["bt_score"], v["attack_score"]
    brank, arank = ranks(bt), ranks(at)
    col1 = {p: ramp(HOT, brank[p]) for p in bt}
    col1.update({t: ramp(COLD, arank[t]) for t in at})
    img1 = panel(ids, (y0, y1, x0, x1), scale, col1, base)

    # panel 2: forces
    count = {}
    for d in divs:
        if d.owner == tag:
            count[d.province] = count.get(d.province, 0) + 1
    foe = v["foe_power"]
    cmax, fmax = max(count.values(), default=1), max(foe.values(), default=1) or 1
    col2 = {p: ramp(GREEN, n / cmax) for p, n in count.items()}
    col2.update({p: ramp(RED, f / fmax) for p, f in foe.items()})
    img2 = panel(ids, (y0, y1, x0, x1), scale, col2, base)

    def xy(p):
        pr = m.provinces[p]
        return (pr.x - x0) * scale, (pr.y - y0) * scale

    f_small, f_big = font(9 + 2 * scale), font(14 + 2 * scale)
    d1, d2 = ImageDraw.Draw(img1), ImageDraw.Draw(img2)
    for p, s in bt.items():
        x, y = xy(p)
        d1.text((x, y), "%.2f" % s, fill=(255, 255, 255) if brank[p] > 0.7 else (40, 20, 0), font=f_small,
                anchor="mm")
    for t, s in at.items():
        x, y = xy(t)
        d1.text((x, y), "%.2f" % s, fill=(255, 255, 255) if arank[t] > 0.6 else (10, 30, 60), font=f_small,
                anchor="mm")
    for p in v["breakthroughs"]:
        x, y = xy(p)
        r = 9 * scale
        d1.ellipse((x - r, y - r, x + r, y + r), outline=(0, 0, 0), width=max(2, scale))
        d2.ellipse((x - r, y - r, x + r, y + r), outline=(0, 0, 0), width=max(2, scale))
    for b, sec in v["sectors"].items():
        for q in sec[1:]:
            x, y = xy(q)
            r = 6 * scale
            for d in (d1, d2):
                d.ellipse((x - r, y - r, x + r, y + r), outline=(60, 60, 60), width=max(1, scale // 2 + 1))
    if v["goal"]:
        x, y = xy(v["goal"])
        for d in (d1, d2):
            d.text((x, y - 10 * scale), "*", fill=(0, 0, 0), font=f_big, anchor="mm")
    for p, n in count.items():
        x, y = xy(p)
        d2.text((x, y), str(n), fill=(255, 255, 255) if n / cmax > 0.4 else (0, 40, 0), font=f_small, anchor="mm")
    for p, f in foe.items():
        x, y = xy(p)
        d2.text((x, y), "%.0f" % f, fill=(255, 255, 255) if f / fmax > 0.4 else (80, 0, 0), font=f_small,
                anchor="mm")

    head = 70
    W, H = img1.width + img2.width + 30, img1.height + head + 10
    out = Image.new("RGB", (W, H), (250, 250, 248))
    out.paste(img1, (10, head))
    out.paste(img2, (img1.width + 20, head))
    d = ImageDraw.Draw(out)
    war = "at war" if tgt in enemies else "not at war"
    d.text((10, 8), f"{tag} vs {target}, {now:%d %b %Y %H:00}, {war}: {rep['divisions']} divisions, "
                    f"front {rep['front']} provinces, {rep['breakthroughs']} breakthroughs (big ring, small rings = rest of sector), "
                    f"* = {target}'s top victory point", fill=(0, 0, 0), font=f_big)
    d.text((10, 40), "Valuations: our front by breakthrough score (yellow -> red), "
                     "enemy provinces by attack score (light -> dark blue); colour = rank", fill=(60, 60, 60), font=f_small)
    d.text((img1.width + 20, 40), "Forces: our divisions per province (green, count), "
                                  "enemy organisation x strength (red)", fill=(60, 60, 60), font=f_small)
    path = a.out or os.path.join(REPO, "recordings", "heatmaps",
                                 f"{tag}_{target}_{now:%Y%m%d_%H}_{datetime.datetime.now():%H%M%S}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    print(path)
    if not a.no_open:
        os.startfile(path)


if __name__ == "__main__":
    main()
