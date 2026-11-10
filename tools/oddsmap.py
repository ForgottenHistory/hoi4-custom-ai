"""Map of how likely an attack on each enemy province is to win, as a PNG (outside the game).

    python tools/oddsmap.py [--tag GER] [--target POL] [--out FILE] [--no-open]

For every enemy province with defenders next to ours, all our divisions next to it attack it at once (from every
province they stand in) and hoi4ai.battle estimates the fight from both sides' template stats, live organisation,
strength and supply (left; right with everyone at full organisation), the terrain, its combat width and the rivers crossed. Green = likely win, red = likely loss;
the label is the win chance and the divisions on each side. Empty enemy provinces next to us are pale green (free).
A rough guide only: see hoi4ai/battle.py for what it leaves out.
"""
import argparse
import datetime
import os

from PIL import Image, ImageDraw

import _bridge  # noqa: F401
from controller import country_ids, enemies_of, friends_of, game_time
from heatmap import FOE, FRIEND, OTHER, OURS, SEA, font, panel, ramp
from hoi4ai import battle, mapdata, templates
from hoi4ai.divisions import Snapshotter
from hoi4ai.live import Provinces
from hoi4ai.memory import Process

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ODDS = [(200, 30, 30), (245, 150, 60), (250, 230, 120), (150, 210, 100), (20, 130, 50)]
FREE = (205, 235, 195)


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
    divs = Snapshotter(os.path.join(REPO, ".cache", "oddsmap_snap.tsv")).take()
    foe_tags = set(enemies_of(tag)) | {target}
    hostile = {cid[t] for t in foe_tags if t in cid}
    ours = {me} | {cid[t] for t in friends_of(tag) if t in cid}
    now = game_time(tag)

    proc = Process()
    stats = {}
    for t in {tag} | foe_tags:
        mine = [d for d in divs if d.owner == t]
        if mine:
            for name, st in templates.country(t, mine, proc).items():
                stats[t, name] = st
    try:
        widths = templates.terrain_widths()
    except Exception:
        widths = {}

    def member(d, rested=False):
        st = stats.get((d.owner, d.template), battle.DEFAULT)
        full = st.get("max_org", 0) * 1000 or 40000
        return (st, full if rested else d.share * full, full, d.strength / 100000)

    at = {}
    for d in divs:
        if m.is_land(d.province):
            at.setdefault(d.province, []).append(d)

    odds, rest, free, label = {}, {}, set(), {}
    for p, pr in m.provinces.items():
        if not m.is_land(p) or ctrl[p] not in hostile:
            continue
        sides = [q for q in m.neighbours.get(p, ()) if m.is_land(q) and ctrl[q] in ours]
        if not sides:
            continue
        defenders = [d for d in at.get(p, ()) if d.owner in foe_tags]
        if not defenders:
            free.add(p)
            continue
        froms = [q for q in sides if any(d.owner == tag for d in at.get(q, ()))]
        attackers = [(d, q) for q in froms for d in at[q] if d.owner == tag]
        if not attackers:
            continue
        base, extra = widths.get(pr.terrain, (70, 35))
        width = base + extra * max(0, len(froms) - 1)
        river = max(m.river(q, p) for q in froms)
        a_sup = sum(min(1.0, d.supply / 100000) for d, _ in attackers) / len(attackers)
        d_sup = sum(min(1.0, d.supply / 100000) for d in defenders) / len(defenders)
        args = (pr.terrain, width, len(froms), river, a_sup, d_sup)
        odds[p], _ = battle.fight([member(d) for d, _ in attackers], [member(d) for d in defenders], *args)
        rest[p], _ = battle.fight([member(d, True) for d, _ in attackers], [member(d, True) for d in defenders], *args)
        label[p] = "%d:%d" % (len(attackers), len(defenders))

    # crop around the provinces we rated
    rated = set(odds) | free
    if not rated:
        raise SystemExit("no enemy provinces next to ours")
    xs = [m.provinces[p].x for p in rated]
    ys = [m.provinces[p].y for p in rated]
    pad = 60
    x0, x1 = max(0, int(min(xs)) - pad), min(m.width, int(max(xs)) + pad)
    y0, y1 = max(0, int(min(ys)) - pad), min(m.height, int(max(ys)) + pad)
    scale = max(1, min(4, 1400 // max(1, x1 - x0)))

    def base(p):
        pr = m.provinces.get(p)
        if pr is None or pr.kind != "land":
            return SEA
        c = ctrl[p]
        return OURS if c == me else FRIEND if c in ours else FOE if c in hostile else OTHER

    f = font(8 + 2 * scale)

    def draw(chances):
        col = {p: ramp(ODDS, w) for p, w in chances.items()}
        col.update({p: FREE for p in free})
        img = panel(ids, (y0, y1, x0, x1), scale, col, base)
        d = ImageDraw.Draw(img)
        for p, w in chances.items():
            pr = m.provinces[p]
            d.multiline_text(((pr.x - x0) * scale, (pr.y - y0) * scale), "%d%%\n%s" % (round(w * 100), label[p]),
                             fill=(0, 0, 0), font=f, anchor="mm", align="center", stroke_width=2,
                             stroke_fill=(255, 255, 255))
        return img

    img, img2 = draw(odds), draw(rest)
    head = 60
    out = Image.new("RGB", (img.width * 2 + 30, img.height + head + 10), (250, 250, 248))
    out.paste(img, (10, head))
    out.paste(img2, (img.width + 20, head))
    dd = ImageDraw.Draw(out)
    good = sum(1 for w in odds.values() if w >= 0.6)
    good2 = sum(1 for w in rest.values() if w >= 0.6)
    dd.text((10, 8), f"{tag} vs {target}, {now:%d %b %Y %H:00}: estimated win chance attacking each enemy province "
                     f"with everything next to it. Left: now ({good}/{len(odds)} at 60%+); right: everyone at full "
                     f"organisation ({good2}/{len(rest)})", fill=(0, 0, 0), font=font(16))
    dd.text((10, 34), "green = likely win, red = likely loss; label: chance, our divisions : theirs; pale green = "
                      "empty. Rough: no forts, entrenchment, planning, air or generals.", fill=(60, 60, 60),
            font=font(12))
    path = a.out or os.path.join(REPO, "recordings", "heatmaps",
                                 f"odds_{tag}_{target}_{now:%Y%m%d_%H}_{datetime.datetime.now():%H%M%S}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    print(path)
    for p in sorted(odds, key=odds.get):
        print(p, m.provinces[p].terrain, label[p], "now %d%% rested %d%%" % (odds[p] * 100, rest[p] * 100))
    if not a.no_open:
        os.startfile(path)


if __name__ == "__main__":
    main()
