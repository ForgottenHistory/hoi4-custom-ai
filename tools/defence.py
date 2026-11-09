"""Where a country could defend against an attacker, from the running game (see hoi4ai/defence.py). Sends nothing.

    python tools/defence.py [--tag POL] [--attacker GER] [--with SLO ...] [--out FILE] [--no-open]

The attacker comes with its subjects and faction (friends_of), plus any --with. Prints the candidate lines from
the border inwards (victory points and factories kept, provinces, divisions needed against ours) and the chosen
main and fallback lines, and draws a map: our land by how hard it is for the attacker to reach, the lines, and
the victory points.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

import _bridge  # noqa: F401
from compare_runs import vp_names
from controller import REPO, country_ids, friends_of, game_time
from heatmap import COLD, OTHER, SEA, font, panel, ramp
from hoi4ai import defence, mapdata, templates
from hoi4ai.divisions import Snapshotter
from hoi4ai.gamefiles import GameFiles
from hoi4ai.live import Provinces
from hoi4ai.memory import Process

FOE, MAIN, FALLBACK, LOST = (232, 200, 200), (200, 20, 20), (240, 150, 0), (238, 236, 226)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="POL")
    ap.add_argument("--attacker", default="GER")
    ap.add_argument("--with", dest="also", nargs="*", default=[])
    ap.add_argument("--out")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tag, att = a.tag.upper(), a.attacker.upper()
    files = GameFiles()
    m = mapdata.load(files)
    rules = defence.Rules(files)
    forts, factories = defence.buildings(m)
    tags, cid = country_ids()
    me = cid[tag]
    lp = Provinces().refresh()
    ctrl, owner = lp.controller, lp.owner
    # the attacker and its friends that border us (a faction member across the continent attacks from nowhere)
    border = {int(ctrl[q]) for p in m.provinces if m.is_land(p) and ctrl[p] == me for q in m.land_neighbours(p)}
    attack_tags = sorted(t for t in {att} | set(friends_of(att)) | {t.upper() for t in a.also}
                         if t in cid and (t == att or cid[t] in border))
    attackers = {cid[t] for t in attack_tags}
    every = Snapshotter(os.path.join(REPO, ".cache", "defence_snap.tsv")).take()
    divs = [x for x in every if x.owner == tag]
    foes = [x for x in every if x.owner in attack_tags]
    def army(tags_):
        """(average width, stats per width) of the divisions of TAGS_, from their templates."""
        rows = []
        for tg in tags_:
            rows += templates.country(tg, [x for x in every if x.owner == tg], Process(), files).values()
        n = sum(t["count"] for t in rows)
        w = sum(t["combat_width"] * t["count"] for t in rows)
        if not n or not w:
            return defence.DIVISION_WIDTH, None
        return w / n, {k: sum(t[k] * t["count"] for t in rows) / w for k in ("soft_attack", "defense", "breakthrough")}

    width = foe_width = defence.DIVISION_WIDTH
    ours = theirs = None
    try:
        width, ours = army([tag])
        foe_width, theirs = army(attack_tags)
    except Exception as e:  # without template stats: every division 18 wide, division for division
        print("template stats:", e)

    d = defence.Defence(m, rules, me, forts, factories)
    d.set_forces(ours, theirs)
    if ours:
        print("per width: " + "; ".join(f"{who} " + ", ".join(f"{k} {v:.1f}" for k, v in st.items())
                                        for who, st in ((tag, ours), ("attackers", theirs))))
    lines = d.lines(ctrl, owner, attackers, width, len(foes), foe_width)
    main_line, fallback = d.choose(lines, len(divs))
    floor = 1 - rules.surrender_limit
    names = vp_names(files)
    print(f"{tag} at {game_time(tag):%Y-%m-%d}: {len(divs)} divisions (average width {width:.0f}) against "
          f"{' '.join(attack_tags)} with {len(foes)} (width {foe_width:.0f}); surrender at {rules.surrender_limit:.0%} of victory points "
          f"lost, so keep over {floor:.0%} (+{d.s.buffer:.0%} buffer)")
    print(f"{'reach':>6} {'VP kept':>8} {'factories':>9} {'front':>6} {'need':>6}")
    shown = [l for i, l in enumerate(lines) if i % max(1, len(lines) // 25) == 0 or l in (main_line, fallback)]
    for l in shown:
        mark = " <- main" if l is main_line else " <- fallback" if l is fallback else ""
        warn = "" if l.vp >= floor + d.s.buffer else " (below buffer)" if l.vp > floor else " (capitulates)"
        print(f"{l.t:6.1f} {l.vp:8.0%} {l.factories:9.0%} {len(l.front):6} {l.need:6.0f}{mark}{warn}")
    vps = {p: v for s in m.states.values() for p, v in s.victory_points.items() if m.is_land(p) and owner[p] == me}
    print("victory points by how hard they are to reach (attacker's cost):")
    for p in sorted(vps, key=lambda p: -vps[p])[:12]:
        c = d.reach_cost.get(p, float("inf"))
        side = "main" if p in main_line.keep else "fallback" if fallback and p in fallback.keep else "lost"
        print(f"  {names.get(p, p)!s:20} {vps[p]:4.0f} VP  reach {c:5.1f}  behind {side} line")

    # map: our land by reach (dark: hard to reach), lost land pale, lines drawn as province outlines
    own = [p for p in m.provinces if m.is_land(p) and owner[p] == me]
    xs, ys = [m.provinces[p].x for p in own], [m.provinces[p].y for p in own]
    pad = 40
    x0, x1 = max(0, int(min(xs)) - pad), min(m.width, int(max(xs)) + pad)
    y0, y1 = max(0, int(min(ys)) - pad), min(m.height, int(max(ys)) + pad)
    scale = max(1, min(4, 900 // max(1, x1 - x0)))
    finite = sorted(c for c in d.reach_cost.values() if c < float("inf"))
    top = finite[int(len(finite) * 0.95)] if finite else 1.0
    ids = mapdata.province_image(m)

    def base(p):
        pr = m.provinces.get(p)
        if pr is None or pr.kind != "land":
            return SEA
        if ctrl[p] in attackers:
            return FOE
        if ctrl[p] == me:
            if p not in main_line.keep:
                return LOST
            return ramp(COLD, 0.1 + 0.45 * min(1.0, d.reach_cost[p] / top))
        return OTHER

    img = panel(ids, (y0, y1, x0, x1), scale, {}, base)
    river = mapdata._read_bmp_indexed(mapdata._read_default_map(files)["rivers"])[y0:y1, x0:x1]
    mask = Image.fromarray(((river >= 3) & (river <= 11)).astype(np.uint8) * 255, "L").resize(img.size, Image.NEAREST)
    img.paste((20, 60, 200), mask=mask)
    dr = ImageDraw.Draw(img)
    f_small = font(9 + 2 * scale)

    def xy(p):
        pr = m.provinces[p]
        return (pr.x - x0) * scale, (pr.y - y0) * scale

    rr = 3 + scale
    for line, col in ((fallback, FALLBACK), (main_line, MAIN)):
        if line is None:
            continue
        for p in line.front:
            cx, cy = xy(p)
            dr.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col, outline=(255, 255, 255))
            dr.text((cx, cy), f"{d.need(p, line.approaches[p], width, len(foes) / len(line.front), foe_width):.0f}", fill=(255, 255, 255), font=font(7 + scale),
                    anchor="mm")
    for p, v in sorted(vps.items(), key=lambda x: -x[1])[:12]:
        cx, cy = xy(p)
        dr.regular_polygon((cx, cy - 3 * rr, 2 + rr + v ** 0.5 / 2), 5, fill=(255, 215, 0), outline=(0, 0, 0))
        dr.text((cx + 4 + rr, cy - 3 * rr), f"{names.get(p, p)} {v:.0f}", fill=(20, 20, 20), font=f_small,
                anchor="lm", stroke_width=2, stroke_fill=(255, 255, 255))
    lines_txt = (f"main (red): {main_line.vp:.0%} VP, {main_line.factories:.0%} factories, {len(main_line.front)} "
                 f"provinces, needs {main_line.need:.0f} of {len(divs)} divisions")
    if fallback:
        lines_txt += (f"   fallback (orange): {fallback.vp:.0%} VP, {fallback.factories:.0%} factories, "
                      f"{len(fallback.front)} provinces, needs {fallback.need:.0f}")
    dr.text((8, 8), lines_txt, fill=(0, 0, 0), font=f_small, stroke_width=2, stroke_fill=(255, 255, 255))
    dr.text((8, 8 + 14 + 2 * scale), "blue: kept (darker = harder for the attacker to reach); pale: given up; dark blue lines: rivers; "
                                    "dots: divisions a line province needs", fill=(40, 40, 40), font=f_small,
            stroke_width=2, stroke_fill=(255, 255, 255))
    path = os.path.abspath(a.out or os.path.join(REPO, "recordings", "heatmaps", f"defence_{tag}.png"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print(path)
    if not a.no_open and sys.platform == "win32":
        os.startfile(path)


if __name__ == "__main__":
    main()
