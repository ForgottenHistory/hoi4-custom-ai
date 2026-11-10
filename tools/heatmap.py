"""Draw what the planner sees: valuations and forces on the map, as a PNG (outside the game).

    python tools/heatmap.py [--tag GER] [--target POL] [--out FILE] [--no-open]

Runs one planning step on the live game (sends nothing) and draws two panels around the front with the target:
  routes: each enemy province's cost to the goal (the target's top victory point, starred), counting terrain
          and defenders (cheap = dark blue); our front by its best route (cheap = dark red). The fast army's
          sector is ringed purple with the way it would push; infantry support points are ringed black.
  forces: infantry+fast divisions per province (green; purple where fast divisions stand), enemy strength
          (red, organisation x strength).
  supply: each supply area (provinces nearest a hub) by our divisions there against the limit learned from
          their supply this step (green fine, orange to red overloaded); hubs labelled "divisions/limit supply".
"""
import argparse
import datetime
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import _bridge  # noqa: F401
from controller import country_ids, enemies_of, friends_of, game_time
from hoi4ai import mapdata, units
from hoi4ai.divisions import Snapshotter
from hoi4ai.front import Planner
from hoi4ai.live import Provinces

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEA, OTHER, OURS, FOE, FRIEND = (172, 198, 222), (222, 219, 210), (236, 232, 220), (240, 222, 222), (228, 232, 222)
HOT = [(255, 245, 170), (253, 174, 60), (215, 48, 31), (120, 0, 30)]
COLD = [(222, 235, 247), (130, 180, 215), (33, 102, 172), (8, 40, 100)]
GREEN = [(220, 240, 210), (120, 195, 110), (20, 120, 50), (0, 60, 25)]
PURPLE = [(225, 205, 240), (170, 110, 210), (110, 30, 160), (60, 0, 100)]
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
    fast_units = {n for n, u in units.load().items() if u.fast}
    pl = Planner(m, me, fast_units)
    orders, rep = pl.plan(ctrl, divs, tag, enemies, tgt, tgt in enemies, friends)
    v = pl.view
    hostile = set(enemies) | {tgt}
    ours = {me} | set(friends)

    # crop around the front and the target country
    cont = m.provinces[v["goal"]].continent if v["goal"] else None
    focus = {p for p in set(v["main"]) | {q for q in m.provinces if m.is_land(q) and ctrl[q] == tgt}
             if cont is None or m.provinces[p].continent == cont}
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

    # panel 1: routes to the goal (cost: terrain x defenders); cheap = dark
    fr = {p: c for p, c in v["front_route"].items() if c < float("inf")}
    route = {t: c for t, c in v["route"].items() if ctrl[t] == tgt}
    frank, rrank = ranks({p: -c for p, c in fr.items()}), ranks({t: -c for t, c in route.items()})
    col1 = {p: ramp(HOT, frank[p]) for p in fr}
    col1.update({t: ramp(COLD, rrank[t]) for t in route})
    img1 = panel(ids, (y0, y1, x0, x1), scale, col1, base)

    # panel 2: forces
    line_n, fast_n = {}, {}
    for d in divs:
        if d.owner == tag:
            tgt_map = fast_n if d.id in v["fast"] else line_n
            tgt_map[d.province] = tgt_map.get(d.province, 0) + 1
    count = {p: line_n.get(p, 0) + fast_n.get(p, 0) for p in set(line_n) | set(fast_n)}
    foe = v["foe_power"]
    cmax, fmax = max(count.values(), default=1), max(foe.values(), default=1) or 1
    col2 = {p: ramp(GREEN, n / cmax) for p, n in count.items()}
    col2.update({p: ramp(PURPLE, 0.35 + 0.65 * n / cmax) for p, n in count.items() if fast_n.get(p)})
    col2.update({p: ramp(RED, f / fmax) for p, f in foe.items()})
    img2 = panel(ids, (y0, y1, x0, x1), scale, col2, base)

    def xy(p):
        pr = m.provinces[p]
        return (pr.x - x0) * scale, (pr.y - y0) * scale

    f_small, f_big = font(9 + 2 * scale), font(14 + 2 * scale)
    d1, d2 = ImageDraw.Draw(img1), ImageDraw.Draw(img2)
    for p, c in fr.items():
        d1.text(xy(p), "%.0f" % c, fill=(255, 255, 255) if frank[p] > 0.7 else (40, 20, 0), font=f_small, anchor="mm")
    for t, c in route.items():
        d1.text(xy(t), "%.0f" % c, fill=(255, 255, 255) if rrank[t] > 0.6 else (10, 30, 60), font=f_small, anchor="mm")
    # the fast army's way: from its point, always on to the cheaper neighbour
    for b, (kind, sec) in v["sectors"].items():
        if kind != "fast":
            continue
        froute = v["fast_route"]
        cur = min((t for t in m.neighbours.get(b, ()) if t in froute), key=froute.get, default=None)
        pts = [xy(b)]
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            pts.append(xy(cur))
            nxt = min((n for n in m.neighbours.get(cur, ()) if n in froute), key=froute.get, default=None)
            cur = nxt if nxt is not None and froute[nxt] < froute[cur] else None
        for d in (d1, d2):
            d.line(pts, fill=(120, 0, 160), width=max(3, 2 * scale))
    for b, (kind, sec) in v["sectors"].items():
        colour = (120, 0, 160) if kind == "fast" else (0, 0, 0)
        for i, q in enumerate(sec):
            x, y = xy(q)
            r = (9 if i == 0 else 6) * scale
            for d in (d1, d2):
                d.ellipse((x - r, y - r, x + r, y + r), outline=colour, width=max(2, scale) if i == 0 else max(1, scale // 2 + 1))
    for p, vp in v["objectives"].items():  # victory points: a star, bigger for more
        x, y = xy(p)
        r = (2 + min(6, vp / 4)) * scale
        for d in (d1, d2):
            d.regular_polygon((x, y - 8 * scale, r), 5, fill=(255, 215, 0), outline=(0, 0, 0))
    for p, n in count.items():
        f = fast_n.get(p, 0)
        label = "%d+%d" % (n - f, f) if f else str(n)
        d2.text(xy(p), label, fill=(255, 255, 255) if f or n / cmax > 0.4 else (0, 40, 0), font=f_small, anchor="mm")
    for p, f in foe.items():
        d2.text(xy(p), "%.0f" % f, fill=(255, 255, 255) if f / fmax > 0.4 else (80, 0, 0), font=f_small, anchor="mm")

    # panel 3: supply. Each supply area (the provinces nearest a hub) by how loaded it is: our divisions there
    # against the limit learned from their supply (green: fine or never short; orange to dark red: overloaded).
    # Hubs are squares labelled "divisions/limit"; thick lines are area borders.
    in_area, sup = {}, {}
    for d in divs:
        if d.owner == tag and m.is_land(d.province):
            a_ = pl.area(d.province)
            in_area[a_] = in_area.get(a_, 0) + 1
            sup.setdefault(a_, []).append(min(1.0, d.supply / 100000))
    cap = pl.supply_cap
    OK = (170, 215, 160)

    def area_colour(a_):
        n, c = in_area.get(a_, 0), cap.get(a_)
        if not n:
            return None
        if c is None or n <= c:
            return OK
        return ramp(HOT, min(1.0, (n / c - 1) / 1.5))
    col3 = {}
    for p in m.provinces:
        if m.is_land(p) and ctrl[p] in ours:
            c = area_colour(pl.area(p))
            if c:
                col3[p] = c
    img3 = panel(ids, (y0, y1, x0, x1), scale, col3, base)
    sub = ids[y0:y1, x0:x1]
    uniq, inv = np.unique(sub, return_inverse=True)
    amap = np.array([pl.area(int(u)) if m.is_land(int(u)) else -1 for u in uniq])[inv.reshape(sub.shape)]
    aedge = np.zeros(sub.shape, bool)
    aedge[:, 1:] |= amap[:, 1:] != amap[:, :-1]
    aedge[1:, :] |= amap[1:, :] != amap[:-1, :]
    aedge = np.kron(aedge, np.ones((scale, scale), bool))
    arr = np.array(img3)
    arr[aedge] = (60, 60, 60)
    img3 = Image.fromarray(arr, "RGB")
    d3 = ImageDraw.Draw(img3)
    for a_, n in in_area.items():
        if a_ not in m.provinces or not (x0 <= m.provinces[a_].x < x1 and y0 <= m.provinces[a_].y < y1):
            continue
        x, y = xy(a_)
        r = 3 * scale
        d3.rectangle((x - r, y - r, x + r, y + r), fill=(30, 30, 30))
        c = cap.get(a_)
        s_ = sup[a_]
        label = f"{n}/{c:.0f} {sum(s_) / len(s_):.0%}" if c is not None else f"{n} {sum(s_) / len(s_):.0%}"
        d3.text((x, y + 6 * scale), label, fill=(0, 0, 0), font=f_small, anchor="mt",
                stroke_width=2, stroke_fill=(255, 255, 255))

    head = 70
    W, H = img1.width + img2.width + img3.width + 40, img1.height + head + 10
    out = Image.new("RGB", (W, H), (250, 250, 248))
    out.paste(img1, (10, head))
    out.paste(img2, (img1.width + 20, head))
    out.paste(img3, (img1.width + img2.width + 30, head))
    d = ImageDraw.Draw(out)
    war = "at war" if tgt in enemies else "not at war"
    d.text((10, 8), f"{tag} vs {target}, {now:%d %b %Y %H:00}, {war}: {rep['divisions']} divisions ({rep['fast']} fast), "
                    f"front {rep['front']}; purple = fast army sector and its way, black = infantry support points, "
                    f"stars = {target}'s victory points", fill=(0, 0, 0), font=f_big)
    d.text((10, 40), "Routes to victory points (terrain x defenders, rivers), cheap = dark; "
           "our front: best route out",
           fill=(60, 60, 60), font=f_small)
    d.text((img1.width + 20, 40), "Forces: infantry+fast per province (purple = fast there), "
                                  "enemy organisation x strength (red)", fill=(60, 60, 60), font=f_small)
    d.text((img1.width + img2.width + 30, 40), "Supply areas: our divisions / learned limit, average supply "
           "(green = fine, orange to red = overloaded)", fill=(60, 60, 60), font=f_small)
    path = a.out or os.path.join(REPO, "recordings", "heatmaps",
                                 f"{tag}_{target}_{now:%Y%m%d_%H}_{datetime.datetime.now():%H%M%S}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    print(path)
    if not a.no_open:
        os.startfile(path)


if __name__ == "__main__":
    main()
