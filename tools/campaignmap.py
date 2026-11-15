"""Maps of a recorded campaign: the player's land as it grew, and where the army went.

    python tools/campaignmap.py RECORDING_DIR [--save SAVE.hoi4 ...] [--tag NGR] [--pad 60] [--gif]
                                [--out FILE] [--no-open]

The recording logs no state ownership, so the land comes from the game's history files at the start (through
the recording's mods, its dlc_load.json) and from each --save (plain-text saves: the owner of every state and
the controller of every province held by someone else). One panel per date (the start, then each save), coloured
by who holds each province: the player's starting land, land the player gained, land the player occupies
without owning it, and every other country in its own muted colour with its tag. The player's divisions that
day (unit_snapshots.tsv) are drawn as dots, with how many stand in a province. A last panel sums the army's
division-days per province over the whole recording, over the land at the end, with the wars (diplomacy.tsv).
The crop covers the player's land at the end and where their divisions went, with --pad pixels around it.
--gif also writes the army's position every 7 days as an animation, with enemy divisions in red; the land between saves is estimated from the
armies' daily movements and the wars (see Replay), checked against the state count in country.tsv.
"""
import argparse
import collections
import datetime
import hashlib
import os
import re
import sys

import numpy as np
from PIL import Image, ImageDraw

import _bridge  # noqa: F401
from examples import player_of, rows, when
from heatmap import SEA, font, panel, ramp
from hoi4ai import mapdata
from hoi4ai.gamefiles import GameFiles

START_LAND, GAINED, OCCUPIED, LOST = (40, 120, 70), (120, 200, 110), (190, 225, 150), (170, 150, 150)
DIV, FOE_DIV = (20, 30, 60), (190, 30, 30)
HEAT = [(250, 240, 200), (250, 180, 80), (220, 70, 40), (110, 0, 30)]


def tag_colour(tag):
    """A muted colour per country, the same every run."""
    h = hashlib.md5(tag.encode()).digest()
    return tuple(150 + h[i] % 80 for i in range(3))


def start_owners(files):
    """{state id: owner tag} from history/states (the undated owner)."""
    out = {}
    for path in files.listdir("history/states", ".txt").values():
        text = re.sub(r"#[^\n]*", "", open(path, encoding="utf-8-sig", errors="replace").read())
        sid = re.search(r"\bid\s*=\s*(\d+)", text)
        hist = text.find("history")
        own = re.search(r"\bowner\s*=\s*(\w+)", text[hist:]) if hist >= 0 else None
        if sid and own:
            out[int(sid.group(1))] = own.group(1)
    return out


def read_save(path):
    """(date, {state id: owner}, {province id: controller where the save names one}) from a plain-text save."""
    owners, control, date, block, key = {}, {}, None, None, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if block is None:
                if date is None and line.startswith("date="):
                    date = line.split("=", 1)[1].strip().strip('"')
                elif line in ("provinces={\n", "states={\n"):
                    block = line[:-3]
                    if block == "states" and owners:  # a later states= block, not the map's
                        block = None
                continue
            if line == "}\n":
                if block == "states":
                    return date, owners, control
                block = None
                continue
            if line.startswith("\t") and not line.startswith("\t\t"):
                key = line.strip().split("=", 1)[0]
            elif line.startswith("\t\towner=") and block == "states":
                owners[int(key)] = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("\t\tcontroller=") and block == "provinces":
                control[int(key)] = line.split("=", 1)[1].strip().strip('"')
    return date, owners, control


def save_date(s):
    y, mo, d, h = (int(x) for x in s.split("."))
    return datetime.datetime(y, mo, d, h % 24)


def snapshots(path, tag):
    """{day: Counter(province)} of TAG's divisions in unit_snapshots.tsv."""
    out, cols, day = {}, None, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#snap":
                day = p[1]
                cols = [c for c in p[3:] if not c.startswith("class=")]
                out.setdefault(day, collections.Counter())
                continue
            if cols is None or len(p) < 3 or p[1] != tag:
                continue
            r = dict(zip(cols, p[1:]))
            try:
                out[day][int(r["i1e0.a4"])] += 1
            except (KeyError, ValueError):
                pass
    return out


def wars(folder, tag):
    """[(enemy, start, end or None)] of TAG's wars, declared by them or on them."""
    out, open_ = [], {}
    for r in rows(os.path.join(folder, "diplomacy.tsv")):
        if r.get("kind") != "pact" or r.get("key") != "war_relation" or tag not in (r.get("country"), r.get("partner")):
            continue
        foe = r["partner"] if r["country"] == tag else r["country"]
        if r["change"] in ("new", "start"):  # the game's own date of the war (logged some days later)
            m = re.search(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", r.get("value", ""))
            open_[(r["country"], foe)] = datetime.datetime(*map(int, m.groups()[:3]), int(m.group(4)) % 24) if m else when(r["date"])
        elif r["change"] == "removed" and (r["country"], foe) in open_:
            out.append((foe, open_.pop((r["country"], foe)), when(r["date"])))
    return sorted(out + [(e, s, None) for (_, e), s in open_.items()], key=lambda w: w[1])


def capitulations(folder):
    """[(country, game date)] from the capitulated_date values in diplomacy.tsv."""
    out = []
    for r in rows(os.path.join(folder, "diplomacy.tsv")):
        if r.get("key") == "capitulated_date" and r["change"] in ("new", "start"):
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", r.get("value", ""))
            if m:
                out.append((r["country"], datetime.date(*map(int, m.groups()))))
    return out


def state_counts(folder):
    """[(date, controlled states)] of the player from country.tsv."""
    out = []
    for r in rows(os.path.join(folder, "country.tsv")):
        try:
            out.append((datetime.datetime.fromisoformat(r["date"][:10]), int(float(r["num_of_controlled_states"]))))
        except (KeyError, ValueError):
            pass
    return out


def tracks(path, tags):
    """{day: {division: (owner, province)}} of the divisions of TAGS in unit_snapshots.tsv."""
    out, cols, day, handle = {}, None, None, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#snap":
                day, handle = p[1], p[2]
                cols = [c for c in p[3:] if not c.startswith("class=")]
                out.setdefault(day, {})
                continue
            if cols is None or len(p) < 3 or p[1] not in tags:
                continue
            try:
                out[day][(handle, p[0])] = (p[1], int(p[1 + cols.index("i1e0.a4")]))
            except (ValueError, IndexError):
                pass
    return out


class Replay:
    """The land on each day between saves, replayed from the divisions' daily positions.

    From each save on: when an enemy capitulates, all its land is the player's to control. While the player is at war, a province their divisions pass through (the shortest land path
    between yesterday's province and today's) becomes theirs to control when its controller or owner is an enemy,
    or when it is the player's in the next save; enemy divisions passing through the player's occupied provinces
    give them back to their owner, and the player's own ones to the enemy. When a war ends, the occupied land that
    is the player's in the next save becomes theirs, with the rest of each such state that is theirs in the next
    save; the rest goes back to its owner. Outside wars, a state that is the player's in the next save becomes
    theirs when their divisions first enter it, and a state they lose goes at the last fall of their state count
    (country.tsv) before the next save. Each save resets the land to what it holds.
    """

    def __init__(self, m, lands, moves, wars_, tag, state_of, counts, capit=()):
        self.m, self.tag, self.state_of = m, tag, state_of
        self.days = {}
        self._paths = {}
        prov_of = collections.defaultdict(list)
        for p, s in state_of.items():
            prov_of[s].append(p)
        saves = [(t.date(), held) for t, held in lands]
        held, nxt, prev, done = dict(saves[0][1]), 1, {}, set()
        falls = [t.date() for (t, n), (_, n0) in zip(counts[1:], counts) if n < n0]
        for day in sorted(moves):
            d = datetime.date.fromisoformat(day)
            while nxt < len(saves) and d >= saves[nxt][0]:
                held, nxt = dict(saves[nxt][1]), nxt + 1
            B = saves[nxt][1] if nxt < len(saves) else None
            active = {e for e, s, f in wars_ if s.date() <= d and (f is None or f.date() >= d)}
            ended = {e for e, s, f in wars_ if f is not None and f.date() == d}
            if ended and B is not None:  # peace: occupied land the next save gives us becomes ours
                won = set()
                for p, (o, c) in held.items():
                    if c == tag and o != tag:
                        if B.get(p, ("",))[0] == tag:
                            held[p] = (tag, tag)
                            won.add(state_of.get(p))
                        elif not active:
                            held[p] = (o, o)
                for s in won - {None}:
                    for p in prov_of[s]:
                        if B.get(p, ("",))[0] == tag:
                            held[p] = (tag, tag)
            for c_, cd in capit:  # a capitulated enemy's land all comes under the player's control
                if (c_, cd) not in done and cd <= d and c_ in active:
                    done.add((c_, cd))
                    for q, (o, c) in held.items():
                        if o == c_ and c != tag and m.is_land(q):
                            held[q] = (o, tag)
            here = moves[day]
            for k, (owner, p) in here.items():
                path = self._path(prev.get(k), p)
                if owner == tag:
                    for q in path:
                        o, c = held.get(q, ("", ""))
                        if c == tag:
                            continue
                        mine_next = B is not None and B.get(q, ("",))[0] == tag
                        if active and (c in active or o in active or mine_next):
                            held[q] = (o, tag)
                        elif not active and mine_next and o != tag:  # handed over without a war
                            for r in prov_of.get(state_of.get(q), [q]):
                                if B.get(r, ("",))[0] == tag:
                                    held[r] = (tag, tag)
                elif owner in active:
                    for q in path:
                        o, c = held.get(q, ("", ""))
                        if c == tag:
                            held[q] = (o, o) if o != tag else (tag, owner)
            if B is not None and d in falls and d == max([f for f in falls if f < saves[nxt][0]], default=None):
                for p, (o, c) in held.items():
                    if o == tag and B.get(p, ("",))[0] not in ("", tag):
                        held[p] = B[p]
            prev = {k: v[1] for k, v in here.items()}
            self.days[day] = dict(held)

    def _path(self, a, b):
        """Land provinces from A to B (A excluded), at most 8 steps; [B] when there is none."""
        m = self.m
        if a is None or a == b or not m.is_land(b):
            return [b] if m.is_land(b) else []
        if (a, b) in self._paths:
            return self._paths[(a, b)]
        back, edge = {a: None}, [a]
        for _ in range(8):
            nxt = []
            for p in edge:
                for q in m.land_neighbours(p):
                    if q not in back:
                        back[q] = p
                        nxt.append(q)
            if b in back:
                break
            edge = nxt
        if b in back:
            out, q = [], b
            while q != a:
                out.append(q)
                q = back[q]
            out.reverse()
        else:
            out = [b]
        self._paths[(a, b)] = out
        return out

    def at(self, day):
        return self.days.get(day)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dir")
    ap.add_argument("--save", nargs="*", default=[])
    ap.add_argument("--tag")
    ap.add_argument("--pad", type=int, default=60)
    ap.add_argument("--gif", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    tag = a.tag or player_of(os.path.join(a.dir, "command_record.tsv"))
    load = os.path.join(a.dir, "dlc_load.json")
    files = GameFiles(load=load if os.path.exists(load) else None)
    m = mapdata.load(files)
    ids = mapdata.province_image(m)
    state_of = {p: sid for sid, st in m.states.items() for p in st.provinces}

    snaps = snapshots(os.path.join(a.dir, "unit_snapshots.tsv"), tag)
    days = sorted(snaps)
    t0 = datetime.datetime.fromisoformat(days[0])
    # (date, {province: (owner, controller)})
    lands = [(t0, {p: (o, o) for sid, o in start_owners(files).items() for p in m.states[sid].provinces
                   if sid in m.states})]
    for s in a.save:
        date, owners, control = read_save(s)
        held = {}
        for p, sid in state_of.items():
            o = owners.get(sid)
            if o:
                held[p] = (o, control.get(p, o))
        lands.append((save_date(date), held))
        print(f"{os.path.basename(s)}: {date}, {sum(1 for o in owners.values() if o == tag)} states of {tag}")
    lands.sort(key=lambda x: x[0])
    first = lands[0][1]
    home = {p for p, (o, _) in first.items() if o == tag}

    # crop: the player's land at the end and wherever their divisions stood
    last = lands[-1][1]
    pts = {p for p, (o, c) in last.items() if tag in (o, c) and m.is_land(p)}
    seen = collections.Counter()
    for c in snaps.values():
        seen.update(c)
    pts |= {p for p in seen if p in m.provinces}
    xs = sorted(m.provinces[p].x for p in pts)
    ys = sorted(m.provinces[p].y for p in pts)
    k = max(0, len(xs) // 100)  # a stray landing far away doesn't widen the map
    x0, x1 = max(0, int(xs[k]) - a.pad), min(m.width, int(xs[-1 - k]) + a.pad)
    y0, y1 = max(0, int(ys[k]) - a.pad), min(m.height, int(ys[-1 - k]) + a.pad)
    scale = max(1, min(4, 900 // max(1, x1 - x0)))
    crop = (y0, y1, x0, x1)
    f_small, f_mid, f_big = font(9 + scale), font(11 + 2 * scale), font(16 + 2 * scale)

    def xy(p):
        pr = m.provinces[p]
        return (pr.x - x0) * scale, (pr.y - y0) * scale

    def political(held, dim=False):
        def base(p):
            pr = m.provinces.get(p)
            if pr is None or pr.kind != "land":
                return SEA
            o, c = held.get(p, ("", ""))
            if o == tag:
                col = START_LAND if p in home else GAINED
            elif c == tag:
                col = OCCUPIED
            elif p in home:
                col = LOST
            else:
                col = tag_colour(c or o) if (c or o) else (210, 210, 210)
            if dim:
                col = tuple(int(v * 0.55 + 255 * 0.45) for v in col)
            return col
        img = panel(ids, crop, scale, {}, base)
        d = ImageDraw.Draw(img)
        # each country's tag at the middle of its land on the panel
        by = collections.defaultdict(list)
        for p, (o, c) in held.items():
            pr = m.provinces.get(p)
            if pr and pr.kind == "land" and x0 <= pr.x < x1 and y0 <= pr.y < y1:
                by[c or o].append(p)
        for t, ps in by.items():
            if len(ps) < 3:
                continue
            cx = float(np.median([xy(p)[0] for p in ps]))
            cy = float(np.median([xy(p)[1] for p in ps]))
            d.text((cx, cy), t, fill=(255, 255, 255) if t == tag else (60, 60, 60), font=f_mid if t == tag else f_small,
                   anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0) if t == tag else (240, 240, 240))
        return img, d

    def dots(d, counter, fill=None):
        fill = fill or DIV
        rr = 3 + scale
        for p, n in counter.items():
            if p not in m.provinces:
                continue
            cx, cy = xy(p)
            if not (0 <= cx < (x1 - x0) * scale and 0 <= cy < (y1 - y0) * scale):
                continue
            r = rr + min(4, n // 3)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=(255, 255, 255))
            if n > 1:
                d.text((cx, cy), str(n), fill=(255, 255, 255), font=f_small, anchor="mm")

    def nearest_day(t):
        return min(days, key=lambda s: abs(datetime.datetime.fromisoformat(s) - t))

    def stats(held):
        own = {p for p, (o, _) in held.items() if o == tag and m.is_land(p)}
        states = {state_of[p] for p in own if p in state_of}
        occ = sum(1 for p, (o, c) in held.items() if c == tag and o != tag and m.is_land(p))
        return f"{len(states)} states, {len(own)} provinces owned" + (f", {occ} occupied" if occ else "")

    panels = []
    for t, held in lands:
        img, d = political(held)
        day = nearest_day(t)
        dots(d, snaps[day])
        panels.append((img, t.strftime("%d %b %Y"),
                       f"{stats(held)}; {sum(snaps[day].values())} divisions"))

    # army footprint over the land at the end
    img, d = political(lands[-1][1], dim=True)
    hi = max(seen.values()) if seen else 1
    for p, n in seen.items():
        if p not in m.provinces:
            continue
        cx, cy = xy(p)
        r = 2 + scale + 6 * (n / hi) ** 0.5
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ramp(HEAT, (np.log1p(n) / np.log1p(hi))), outline=(90, 40, 30))
    ws = wars(a.dir, tag)
    lines = [f"{e} from {s:%d %b %Y}" + (", still on" if f is None else f", {(f - s).days} days") for e, s, f in ws]
    panels.append((img, "Where the army went", f"division-days per province, {days[0]} .. {days[-1]}"))

    head, gap = 64, 14
    pw, ph = panels[0][0].width, panels[0][0].height
    cols = 2 if len(panels) > 2 else len(panels)
    nrows = (len(panels) + cols - 1) // cols
    foot = 60 + 20 * ((len(lines) + 3) // 4)
    W = cols * pw + gap * (cols + 1)
    H = nrows * (ph + head) + foot
    out = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(out)
    for i, (img, label, sub) in enumerate(panels):
        x, y = gap + (i % cols) * (pw + gap), (i // cols) * (ph + head)
        out.paste(img, (x, y + head))
        d.text((x, y + 8), label, fill=(0, 0, 0), font=f_big)
        d.text((x, y + 14 + 20 + 2 * scale), sub, fill=(70, 70, 70), font=f_small)
    yy, xx = nrows * (ph + head) + 10, gap
    for kind, col, text in (("box", START_LAND, f"{tag} at the start"), ("box", GAINED, f"gained by {tag}"),
                            ("box", OCCUPIED, f"occupied by {tag}"), ("box", LOST, "lost"),
                            ("dot", DIV, f"{tag} divisions (number in province)"),
                            ("dot", HEAT[2], "army footprint (more days: darker, larger)")):
        if kind == "box":
            d.rectangle((xx, yy, xx + 16, yy + 16), fill=col, outline=(120, 120, 120))
        else:
            d.ellipse((xx, yy, xx + 16, yy + 16), fill=col)
        d.text((xx + 22, yy + 8), text, fill=(30, 30, 30), font=f_small, anchor="lm")
        xx += 30 + int(d.textlength(text, font=f_small)) + 18
    yy += 28
    d.text((gap, yy), "Wars:", fill=(0, 0, 0), font=f_small)
    for i, s in enumerate(lines):
        d.text((gap + 60 + (i % 4) * ((W - 80) // 4), yy + 20 * (i // 4)), s, fill=(40, 40, 40), font=f_small)

    rec = os.path.basename(os.path.normpath(a.dir))
    path = os.path.abspath(a.out or os.path.join(a.dir, f"campaignmap_{tag}.png"))
    out.save(path)
    print(path)

    if a.gif:
        counts = state_counts(a.dir)
        moves = tracks(os.path.join(a.dir, "unit_snapshots.tsv"), {tag} | {e for e, _, _ in ws})
        rep = Replay(m, lands, moves, ws, tag, state_of, counts, capitulations(a.dir))
        # how well the replay follows the recorded count of controlled states (most provinces of the state)
        errs = []
        for t, n in counts[::7]:
            held = rep.at(t.date().isoformat())
            if held is None:
                continue
            per = collections.defaultdict(lambda: [0, 0])
            for p, (o, c) in held.items():
                if p in state_of and m.is_land(p):
                    per[state_of[p]][0] += c == tag
                    per[state_of[p]][1] += 1
            errs.append(abs(sum(1 for k, v in per.values() if k * 2 > v) - n))
        print(f"replayed land vs country.tsv controlled states: mean off by {np.mean(errs):.1f} states, "
              f"exact on {sum(e == 0 for e in errs)}/{len(errs)} checks")
        frames = []
        save_days = {x[0].date() for x in lands}
        for day in days[::7] + ([days[-1]] if (len(days) - 1) % 7 else []):
            held = rep.at(day)
            img, d = political(held)
            at_war = [e for e, s, f in ws if s.date() <= datetime.date.fromisoformat(day)
                      and (f is None or f.date() >= datetime.date.fromisoformat(day))]
            dots(d, collections.Counter(p for o, p in moves[day].values() if o in at_war), FOE_DIV)
            dots(d, snaps[day])
            d.rectangle((0, 0, img.width, 30 + 2 * scale), fill=(250, 250, 248))
            d.text((8, 4), f"{day}  {sum(snaps[day].values())} divisions" + (f"  at war: {' '.join(at_war)}"
                                                                               if at_war else ""),
                   fill=(180, 20, 20) if at_war else (0, 0, 0), font=f_mid)
            d.text((img.width - 8, 4), "land from save" if datetime.date.fromisoformat(day) in save_days
                   else "land replayed from the armies", fill=(110, 110, 110), font=f_small, anchor="ra")
            frames.append(img)
            t += datetime.timedelta(days=7)
        gpath = os.path.splitext(path)[0] + ".gif"
        frames[0].save(gpath, save_all=True, append_images=frames[1:], duration=180, loop=0, optimize=True)
        print(gpath, len(frames), "frames")
    if not a.no_open:
        os.startfile(path) if hasattr(os, "startfile") else None
    return rec


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
