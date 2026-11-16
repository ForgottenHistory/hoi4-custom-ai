"""A political map PNG of Europe from a state_snapshot.py snapshot (control_now.tsv, states_now.tsv).

    python tools/map_now.py RECORDING_DIR [--tag GER] [--scale 3] [--out FILE] [--no-open]

Every land province is coloured by who controls it, in the game's own country colours (common/countries colors, mods too; a subject whose autonomy state has use_overlord_color takes its overlord's, from subjects_now.tsv; no colour: hash colour), labelled with the game's own names (names_now.tsv); the land --tag owns but others hold
(or holds without owning) is outlined in the opposite way: owned-but-lost land is paler, land held without owning is
hatched with the owner's colour. Tags are written at the middle of each country's land. The crop is Europe (the
provinces whose continent index is 1, Europe) and the map comes from the recording's mods (dlc_load.json).
"""
import argparse
import collections
import colorsys
import csv
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import _bridge  # noqa: F401
from campaignmap import tag_colour
from heatmap import SEA, font, panel
from hoi4ai import mapdata
from hoi4ai.gamefiles import GameFiles


def read_colours(files):
    """{tag: (r, g, b)}: the `color` of each tag in common/countries/*colors*.txt (base game, then mods in load order).
    Written rgb { r g b } (0-255) or HSV { h s v } (0-1)."""
    out = {}
    for name, path in sorted(files.listdir("common/countries", ".txt").items()):
        if "color" not in name.lower():
            continue
        text = re.sub(r"#[^\n]*", "", open(path, encoding="utf-8-sig", errors="replace").read())
        for m in re.finditer(r"\b(\w+)\s*=\s*\{\s*color\s*=\s*(\w*)\s*\{([^}]*)\}", text):
            v = [float(x) for x in m.group(3).split()]
            if len(v) != 3:
                continue
            if m.group(2).lower() == "hsv":
                v = [c * 255 for c in colorsys.hsv_to_rgb(*v)]
            elif max(v) <= 1.0:
                v = [c * 255 for c in v]
            out[m.group(1)] = tuple(int(round(c)) for c in v)
    return out


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dir")
    ap.add_argument("--tag", default="GER")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--out")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    load = os.path.join(a.dir, "dlc_load.json")      # read from the recording; everything written goes inside this project
    a.dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordings",
                         os.path.basename(os.path.normpath(a.dir)))
    os.makedirs(a.dir, exist_ok=True)
    files = GameFiles(load=load if os.path.exists(load) else None)
    m = mapdata.load(files)
    ids = mapdata.province_image(m)
    control = {int(r["province"]): r["controller"] for r in read(os.path.join(a.dir, "control_now.tsv"))}
    day = read(os.path.join(a.dir, "control_now.tsv"))[0]["date"]
    owner = {int(r["state"]): r["owner"] for r in read(os.path.join(a.dir, "states_now.tsv"))}
    state_of = {p: sid for sid, st in m.states.items() for p in st.provinces}

    eu = [p for p in m.provinces.values() if p.kind == "land" and p.continent == 1]
    xs, ys = sorted(p.x for p in eu), sorted(p.y for p in eu)
    k = max(0, len(xs) // 200)
    x0, x1 = max(0, int(xs[k]) - 20), min(m.width, int(xs[-1 - k]) + 20)
    y0, y1 = max(0, int(ys[k]) - 20), min(m.height, int(ys[-1 - k]) + 20)
    crop, scale = (y0, y1, x0, x1), a.scale

    game = read_colours(files)
    # a subject whose autonomy state has use_overlord_color is drawn in its overlord's colour (subjects_now.tsv)
    path = os.path.join(a.dir, "subjects_now.tsv")
    overlord = {r["subject"]: r["overlord"] for r in read(path) if r["overlord_colour"] == "yes"} if os.path.exists(path) else {}
    path = os.path.join(a.dir, "names_now.tsv")
    names = {r["tag"]: r["name"] for r in read(path)} if os.path.exists(path) else {}

    def colour(t):
        for _ in range(5):                                # an overlord can itself be a subject
            if t not in overlord:
                break
            t = overlord[t]
        return game.get(t) or tag_colour(t)

    def base(p):
        pr = m.provinces.get(p)
        if pr is None or pr.kind != "land":
            return SEA
        c = control.get(p)
        if not c:
            return (210, 210, 210)
        o = owner.get(state_of.get(p), c)
        col = colour(c)
        if o == a.tag and c != a.tag:                     # ours, held by someone else
            col = tuple(int(v * 0.5 + 255 * 0.5) for v in colour(c))
        return col

    img = panel(ids, crop, scale, {}, base)
    # country borders: bold black where two land provinces have different controllers
    index = {t: i + 1 for i, t in enumerate(sorted(set(control.values())))}
    who = np.zeros(int(ids.max()) + 1, np.int32)
    for p, c in control.items():
        pr = m.provinces.get(p)
        if pr is not None and pr.kind == "land" and p < len(who):
            who[p] = index[c]
    sub = who[ids[y0:y1, x0:x1]]
    edge = np.zeros(sub.shape, bool)
    edge[:, 1:] |= (sub[:, 1:] != sub[:, :-1]) & (sub[:, 1:] > 0) & (sub[:, :-1] > 0)
    edge[1:, :] |= (sub[1:, :] != sub[:-1, :]) & (sub[1:, :] > 0) & (sub[:-1, :] > 0)
    mask = Image.fromarray(edge.astype(np.uint8) * 255).resize(img.size, Image.NEAREST).filter(ImageFilter.MaxFilter(3))
    img.paste((0, 0, 0), mask=mask)
    d = ImageDraw.Draw(img)
    by = collections.defaultdict(list)
    for p, c in control.items():
        pr = m.provinces.get(p)
        if pr and pr.kind == "land" and x0 <= pr.x < x1 and y0 <= pr.y < y1:
            by[c].append(p)
    big, small = font(14 + 2 * scale), font(8 + 2 * scale)
    for t, ps in by.items():
        if len(ps) < 4:
            continue
        cx = float(np.median([(m.provinces[p].x - x0) * scale for p in ps]))
        cy = float(np.median([(m.provinces[p].y - y0) * scale for p in ps]))
        mine = t == a.tag
        label = names.get(t, t).replace(" ", "\n", 1) if len(names.get(t, t)) > 12 else names.get(t, t)
        d.multiline_text((cx, cy), label, fill=(255, 255, 255) if mine else (50, 50, 50), font=big if mine else small,
                         anchor="mm", align="center", stroke_width=2, stroke_fill=(0, 0, 0) if mine else (240, 240, 240))
    # occupied without owning: a dot in the owner's colour on each such province
    for p, c in control.items():
        pr = m.provinces.get(p)
        o = owner.get(state_of.get(p))
        if pr and c != o and o and pr.kind == "land" and x0 <= pr.x < x1 and y0 <= pr.y < y1 and a.tag in (c, o):
            cx, cy = (pr.x - x0) * scale, (pr.y - y0) * scale
            d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=colour(o if c == a.tag else c), outline=(255, 255, 255))
    d.text((10, 10), f"{day}   in-game colours and names (subjects in their overlord's colour); {a.tag}'s land held by others pale; dots = occupied or lost provinces",
           fill=(20, 20, 20), font=font(16), stroke_width=2, stroke_fill=(255, 255, 255))
    out = a.out or os.path.join(a.dir, f"map_now_{a.tag}.png")
    img.save(out)
    print(out, img.size)
    if not a.no_open and hasattr(os, "startfile"):
        os.startfile(out)


if __name__ == "__main__":
    main()
