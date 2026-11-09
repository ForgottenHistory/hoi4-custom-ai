"""Maps of recorded wars at the same day of each: who holds what, and where the divisions stand.

    python tools/runmap.py RUN_FOLDER [RUN_FOLDER ...] [--day 15] [--labels You "Game AI" ...] [--out FILE] [--no-open]

One panel per run (tools/battlerec.py or a controller run), at the snapshot nearest to DAY days after the
declaration: land taken from the target, the target's land still held, our divisions (fast and infantry
apart, with how many stand in a province) and the target's, and the target's four most valuable victory points
(names from the game's localisation), or the --cities named. Panels go side by side in one PNG.
"""
import argparse
import collections
import datetime
import os
import sys

from PIL import Image, ImageDraw

import _bridge  # noqa: F401
from compare_runs import FMT, Run, vp_names
from controller import REPO
from heatmap import OTHER, SEA, font, panel
from hoi4ai import mapdata, units
from hoi4ai.gamefiles import GameFiles

HOME, TAKEN, HELD = (214, 219, 228), (126, 156, 204), (238, 196, 178)
FAST, INF, FOE = (24, 48, 112), (70, 110, 180), (178, 34, 34)


def snapshot_at(run, day):
    war = next(s["date"] for s in run.status if s["at_war"] == "1")
    t0 = datetime.datetime.strptime(war, FMT)
    want = t0 + datetime.timedelta(days=day)
    date = min(run.steps, key=lambda d: abs(datetime.datetime.strptime(d, FMT) - want))
    return war, date, (datetime.datetime.strptime(date, FMT) - t0).total_seconds() / 86400


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--day", type=float, default=15)
    ap.add_argument("--labels", nargs="*", default=[])
    ap.add_argument("--cities", nargs="*", default=[], help="victory points to mark (names, any prefix)")
    ap.add_argument("--out")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    files = GameFiles()
    m = mapdata.load()
    ids = mapdata.province_image(m)
    names = vp_names(files)
    fast_units = {n for n, u in units.load(files).items() if u.fast}
    runs = [Run(f) for f in a.runs]
    labels = a.labels + [r.label for r in runs[len(a.labels):]]

    # one crop for all panels: the target's land at the first run's declaration, and a margin
    r0 = runs[0]
    war0, _, _ = snapshot_at(r0, 0)
    tgt0 = r0.ids[r0.meta["target"]]
    start = r0.control[war0]
    target_land = {p for p, c in start.items() if c == tgt0 and m.is_land(p)}
    xs = [m.provinces[p].x for p in target_land]
    ys = [m.provinces[p].y for p in target_land]
    pad = 70
    x0, x1 = max(0, int(min(xs)) - pad), min(m.width, int(max(xs)) + pad)
    y0, y1 = max(0, int(min(ys)) - pad), min(m.height, int(max(ys)) + pad)
    scale = max(1, min(3, 700 // max(1, x1 - x0)))
    vps = {}
    for st in m.states.values():
        vps.update(st.victory_points)
    top = sorted((p for p in vps if p in target_land), key=lambda p: -vps[p])[:4]
    if a.cities:  # by name, anywhere on the panel (Danzig isn't the target's at the start)
        inside = [p for p in vps if p in m.provinces and x0 <= m.provinces[p].x < x1 and y0 <= m.provinces[p].y < y1]
        top = [max(hit, key=vps.get) for c in a.cities
               for hit in [[p for p in inside if names.get(p, "").lower().startswith(c.lower())]] if hit]
    f_small, f_mid, f_big = font(8 + 2 * scale), font(10 + 2 * scale), font(14 + 3 * scale)

    panels = []
    for run, label in zip(runs, labels):
        tag, target = run.meta["tag"], run.meta["target"]
        me, tgt = run.ids[tag], run.ids[target]
        war, date, at = snapshot_at(run, a.day)
        ctrl = run.control[date]
        first = run.control[war]
        was_target = {p for p, c in first.items() if c == tgt}

        def base(p):
            pr = m.provinces.get(p)
            if pr is None or pr.kind != "land":
                return SEA
            c = ctrl.get(p)
            if c == me:
                return TAKEN if p in was_target else HOME
            return HELD if c == tgt else OTHER

        img = panel(ids, (y0, y1, x0, x1), scale, {}, base)
        d = ImageDraw.Draw(img)

        def xy(p):
            pr = m.provinces[p]
            return (pr.x - x0) * scale, (pr.y - y0) * scale

        here = collections.defaultdict(collections.Counter)
        for x in run.divs[date]:
            p = int(x["province"])
            if p not in m.provinces:
                continue
            kind = ("fast" if x["unit"] in fast_units else "inf") if x["owner"] == tag else (
                "foe" if x["owner"] == target else None)
            if kind:
                here[p][kind] += 1
        rr = 4 + 2 * scale
        for p, c in here.items():
            cx, cy = xy(p)
            if not (0 <= cx < img.width and 0 <= cy < img.height):
                continue
            marks = [(k, n) for k, n in (("fast", c["fast"]), ("inf", c["inf"]), ("foe", c["foe"])) if n]
            for i, (k, n) in enumerate(marks):
                ox = (i - (len(marks) - 1) / 2) * (2 * rr + 1)
                fill = {"fast": FAST, "inf": INF, "foe": FOE}[k]
                if k == "fast":
                    d.rectangle((cx + ox - rr, cy - rr, cx + ox + rr, cy + rr), fill=fill, outline=(255, 255, 255))
                else:
                    d.ellipse((cx + ox - rr, cy - rr, cx + ox + rr, cy + rr), fill=fill, outline=(255, 255, 255))
                d.text((cx + ox, cy), str(n), fill=(255, 255, 255), font=f_small, anchor="mm")
        for p in top:
            cx, cy = xy(p)
            held = ctrl.get(p) == me
            d.regular_polygon((cx, cy - 3 * rr, 3 + 2 * scale), 5, fill=(255, 215, 0) if held else (255, 255, 255),
                              outline=(0, 0, 0))
            d.text((cx + 5 + 2 * scale, cy - 3 * rr), names.get(p, str(p)), fill=(20, 20, 20), font=f_mid, anchor="lm",
                   stroke_width=2, stroke_fill=(255, 255, 255))
        taken = sum(1 for p in was_target if ctrl.get(p) == me and m.is_land(p))
        n_land = sum(1 for p in was_target if m.is_land(p))
        foe = sum(c["foe"] for c in here.values())
        panels.append((img, label, f"day {at:.1f} ({date[:10]}): {taken}/{n_land} provinces taken, {foe} Polish "
                                    f"divisions left"))

    head, foot, gap = 56, 40, 12
    W = sum(p[0].width for p in panels) + gap * (len(panels) + 1)
    H = panels[0][0].height + head + foot
    out = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(out)
    x = gap
    for img, label, sub in panels:
        out.paste(img, (x, head))
        d.text((x, 6), label, fill=(0, 0, 0), font=f_big)
        d.text((x, 6 + 18 + 3 * scale), sub, fill=(70, 70, 70), font=f_small)
        x += img.width + gap
    yy = head + panels[0][0].height + 10
    xx = gap
    for kind, col, text in (("box", TAKEN, "taken from Poland"), ("box", HELD, "still Polish"),
                            ("box", HOME, "German"), ("sq", FAST, "German fast divisions"),
                            ("dot", INF, "German infantry"), ("dot", FOE, "Polish divisions"),
                            ("star", (255, 215, 0), "city taken (white: not yet)")):
        if kind == "box":
            d.rectangle((xx, yy, xx + 16, yy + 16), fill=col, outline=(120, 120, 120))
        elif kind == "sq":
            d.rectangle((xx, yy, xx + 16, yy + 16), fill=col)
        elif kind == "dot":
            d.ellipse((xx, yy, xx + 16, yy + 16), fill=col)
        else:
            d.regular_polygon((xx + 8, yy + 8, 8), 5, fill=col, outline=(0, 0, 0))
        d.text((xx + 22, yy + 8), text, fill=(30, 30, 30), font=f_small, anchor="lm")
        xx += 30 + int(d.textlength(text, font=f_small)) + 18
    path = os.path.abspath(a.out or os.path.join(REPO, "recordings", "heatmaps", f"runs_day{a.day:g}.png"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path)
    print(path)
    if not a.no_open and sys.platform == "win32":
        os.startfile(path)


if __name__ == "__main__":
    main()
