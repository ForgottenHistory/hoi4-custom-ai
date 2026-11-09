"""The map as the game builds it from its files (through the active mods): provinces, neighbours, states.

Neighbours come from provinces.bmp (provinces whose pixels touch, including across the east-west wrap) plus
adjacencies.csv (straits and other crossings; 'impassable' entries remove a border). Built once per set of input
files and cached in .cache/ next to this package.
"""
import csv
import hashlib
import os
import pickle
import re
import struct

import numpy as np

from .gamefiles import GameFiles

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
CACHE_VERSION = 2


class Province:
    __slots__ = ("id", "kind", "coastal", "terrain", "continent", "x", "y", "pixels", "state")

    def __init__(self, pid, kind, coastal, terrain, continent):
        self.id, self.kind, self.coastal, self.terrain, self.continent = pid, kind, coastal, terrain, continent
        self.x = self.y = 0.0
        self.pixels = 0
        self.state = 0

    def __repr__(self):
        return "Province(%d %s %s state=%d)" % (self.id, self.kind, self.terrain, self.state)


class State:
    __slots__ = ("id", "name", "provinces", "victory_points", "category", "file")

    def __init__(self, sid, name, provinces, vps, category, file):
        self.id, self.name, self.provinces, self.victory_points, self.category, self.file = \
            sid, name, provinces, vps, category, file


class Map:
    """provinces {id: Province}; neighbours {id: {id: Edge kind}}; border {(a, b): shared pixel edges, a < b};
    rivers {(a, b): 1 small river | 2 large river along that border, a < b}; states {id: State}."""

    def __init__(self):
        self.provinces, self.neighbours, self.border, self.states = {}, {}, {}, {}
        self.rivers = {}
        self.width = self.height = 0
        self.sources = {}

    def land_neighbours(self, pid):
        return [n for n in self.neighbours.get(pid, ()) if self.provinces[n].kind == "land"]

    def river(self, a, b):
        """0 none, 1 small river, 2 large river between provinces A and B."""
        return self.rivers.get((a, b) if a < b else (b, a), 0)

    def is_land(self, pid):
        p = self.provinces.get(pid)
        return p is not None and p.kind == "land"


def _strip_comments(text):
    return re.sub(r"#[^\n]*", "", text)


def _read_default_map(files):
    names = {"definitions": "definition.csv", "provinces": "provinces.bmp", "adjacencies": "adjacencies.csv",
             "rivers": "rivers.bmp"}
    p = files.find("map/default.map")
    if p:
        for k, v in re.findall(r'(?m)^\s*(\w+)\s*=\s*"([^"]+)"', _strip_comments(open(p, encoding="latin-1").read())):
            if k in names:
                names[k] = v
    return {k: files.find("map/" + v) for k, v in names.items()}


def _read_bmp_indexed(path):
    """8-bit palette BMP -> (H, W) uint8 array of palette indices, top row first."""
    data = open(path, "rb").read()
    off, = struct.unpack_from("<I", data, 10)
    w, h = struct.unpack_from("<ii", data, 18)
    bpp, comp = struct.unpack_from("<HI", data, 28)
    if bpp != 8 or comp != 0:
        raise ValueError("%s: need an 8-bit uncompressed BMP" % path)
    top_down = h < 0
    h = abs(h)
    stride = (w + 3) & ~3
    rows = np.frombuffer(data, np.uint8, stride * h, off).reshape(h, stride)[:, :w]
    return rows if top_down else rows[::-1]


# rivers.bmp palette: 0-2 markers (source, merge, split), 3-11 river width (thin to wide), 254/255 none
RIVER_LARGE_FROM = 7
RIVER_SHARE = 0.25  # share of a border's length that must run along a river for it to be a river crossing


def _river_borders(ids, rivers):
    """{(a, b): 1 small / 2 large} for borders that run along a river."""
    river = (rivers >= 3) & (rivers <= 11)
    large = (rivers >= RIVER_LARGE_FROM) & (rivers <= 11)
    total, on_river, on_large = {}, {}, {}
    for (a, b, ra, rb, la, lb) in (
            (ids[:, :-1], ids[:, 1:], river[:, :-1], river[:, 1:], large[:, :-1], large[:, 1:]),
            (ids[:-1, :], ids[1:, :], river[:-1, :], river[1:, :], large[:-1, :], large[1:, :])):
        m = a != b
        lo = np.minimum(a[m], b[m]).astype(np.int64)
        hi = np.maximum(a[m], b[m]).astype(np.int64)
        key = lo * 1000000 + hi
        r = (ra[m] | rb[m])
        l_ = (la[m] | lb[m])
        for arr, out in ((np.ones_like(r), total), (r, on_river), (l_, on_large)):
            uk, cnt = np.unique(key[arr.astype(bool)], return_counts=True)
            for k, c in zip(uk.tolist(), cnt.tolist()):
                out[k] = out.get(k, 0) + c
    res = {}
    for k, n in total.items():
        rv = on_river.get(k, 0)
        if n and rv / n >= RIVER_SHARE:
            res[(k // 1000000, k % 1000000)] = 2 if on_large.get(k, 0) * 2 >= rv else 1
    return res


def _read_bmp(path):
    """24-bit uncompressed BMP -> (H, W) int32 array of 0xRRGGBB, top row first."""
    data = open(path, "rb").read()
    if data[:2] != b"BM":
        raise ValueError("%s: not a BMP" % path)
    off, = struct.unpack_from("<I", data, 10)
    w, h = struct.unpack_from("<ii", data, 18)
    bpp, comp = struct.unpack_from("<HI", data, 28)
    if bpp != 24 or comp != 0:
        raise ValueError("%s: need a 24-bit uncompressed BMP (got %d bpp, compression %d)" % (path, bpp, comp))
    top_down = h < 0
    h = abs(h)
    stride = (w * 3 + 3) & ~3
    rows = np.frombuffer(data, np.uint8, stride * h, off).reshape(h, stride)[:, :w * 3].reshape(h, w, 3)
    if not top_down:
        rows = rows[::-1]
    rows = rows.astype(np.int32)
    return (rows[..., 2] << 16) | (rows[..., 1] << 8) | rows[..., 0]  # BMP stores B, G, R


def _read_definitions(path):
    provs, colour = {}, {}
    for line in open(path, encoding="latin-1"):
        p = line.strip().split(";")
        if len(p) < 8 or not p[0].strip().isdigit():
            continue
        pid = int(p[0])
        if pid == 0:
            continue
        r, g, b = int(p[1]), int(p[2]), int(p[3])
        provs[pid] = Province(pid, p[4].strip().lower(), p[5].strip().lower() == "true", p[6].strip().lower(),
                              int(p[7]) if p[7].strip().lstrip("-").isdigit() else 0)
        colour[(r << 16) | (g << 8) | b] = pid
    return provs, colour


def _pixel_graph(ids):
    """{(a, b): number of touching pixel edges}, a < b, 4-neighbourhood, wrapping east-west."""
    h, w = ids.shape
    pairs = [(ids[:, :-1], ids[:, 1:]), (ids[:-1, :], ids[1:, :]), (ids[:, -1:], ids[:, :1])]
    counts = {}
    for a, b in pairs:
        a, b = a.ravel(), b.ravel()
        m = a != b
        a, b = a[m], b[m]
        lo, hi = np.minimum(a, b).astype(np.int64), np.maximum(a, b).astype(np.int64)
        key = lo * 1000000 + hi
        uk, cnt = np.unique(key, return_counts=True)
        for k, c in zip(uk.tolist(), cnt.tolist()):
            pair = (k // 1000000, k % 1000000)
            counts[pair] = counts.get(pair, 0) + c
    return counts


def _read_adjacencies(path):
    out = []
    if not path:
        return out
    with open(path, encoding="latin-1", newline="") as f:
        for row in csv.reader(f, delimiter=";"):
            if len(row) < 3 or not row[0].strip().lstrip("-").isdigit():
                continue
            a, b = int(row[0]), int(row[1])
            if a < 0 or b < 0:
                continue
            through = int(row[3]) if len(row) > 3 and row[3].strip().lstrip("-").isdigit() else -1
            out.append((a, b, row[2].strip().lower() or "land", through))
    return out


_STATE_ID = re.compile(r"\bid\s*=\s*(\d+)")
_STATE_NAME = re.compile(r'\bname\s*=\s*"?([\w.\-]+)"?')
_STATE_PROVS = re.compile(r"\bprovinces\s*=\s*\{([^}]*)\}")
_STATE_VPS = re.compile(r"\bvictory_points\s*=\s*\{\s*(\d+)\s+([\d.]+)\s*\}")
_STATE_CAT = re.compile(r"\bstate_category\s*=\s*\"?(\w+)")


def _read_states(files):
    states = {}
    for name, path in sorted(files.listdir("history/states", ".txt").items()):
        text = _strip_comments(open(path, encoding="utf-8-sig", errors="replace").read())
        sid, provs = _STATE_ID.search(text), _STATE_PROVS.search(text)
        if not sid or not provs:
            continue
        nm, cat = _STATE_NAME.search(text), _STATE_CAT.search(text)
        vps = {int(p): float(v) for p, v in _STATE_VPS.findall(text)}
        states[int(sid.group(1))] = State(int(sid.group(1)), nm.group(1) if nm else "", [int(x) for x in provs.group(1).split()],
                                          vps, cat.group(1) if cat else "", path)
    return states


def _cache_key(files, paths):
    h = hashlib.sha1(str(CACHE_VERSION).encode())
    for p in sorted(paths):
        st = os.stat(p)
        h.update(("%s|%d|%d\n" % (p, st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()[:16]


def _id_image(img, colour):
    keys = np.array(sorted(colour), dtype=np.int64)
    vals = np.array([colour[k] for k in keys.tolist()], dtype=np.int32)
    idx = np.searchsorted(keys, img.ravel()).clip(0, len(keys) - 1)
    known = keys[idx] == img.ravel()
    return np.where(known, vals[idx], 0).reshape(img.shape), vals


def province_image(m):
    """(H, W) int32 array: the province id of each pixel of provinces.bmp (top row first). Cached."""
    st = os.stat(m.sources["provinces"])
    key = hashlib.sha1(("%s|%d|%d" % (m.sources["provinces"], st.st_size, st.st_mtime_ns)).encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, "ids_%s.npy" % key)
    if os.path.exists(path):
        return np.load(path)
    _, colour = _read_definitions(m.sources["definitions"])
    ids, _ = _id_image(_read_bmp(m.sources["provinces"]), colour)
    os.makedirs(CACHE_DIR, exist_ok=True)
    np.save(path, ids)
    return ids


def load(files=None, use_cache=True, verbose=False):
    files = files or GameFiles()
    src = _read_default_map(files)
    for k in ("definitions", "provinces"):
        if not src[k]:
            raise FileNotFoundError("map file for %r not found (%s)" % (k, files.describe()))
    state_files = list(files.listdir("history/states", ".txt").values())
    key = _cache_key(files, [p for p in src.values() if p] + state_files)
    cache = os.path.join(CACHE_DIR, "map_%s.pkl" % key)
    if use_cache and os.path.exists(cache):
        with open(cache, "rb") as f:
            return pickle.load(f)

    if verbose:
        print("building map from", src["provinces"])
    m = Map()
    m.sources = src
    m.provinces, colour = _read_definitions(src["definitions"])
    img = _read_bmp(src["provinces"])
    m.height, m.width = img.shape
    ids, vals = _id_image(img, colour)

    # centroids (x right, y down from the top of provinces.bmp)
    flat = ids.ravel()
    n = int(vals.max()) + 1
    cnt = np.bincount(flat, minlength=n)
    ys, xs = np.divmod(np.arange(flat.size), m.width)
    sx, sy = np.bincount(flat, xs, minlength=n), np.bincount(flat, ys, minlength=n)
    for pid, p in m.provinces.items():
        if pid < n and cnt[pid]:
            p.pixels, p.x, p.y = int(cnt[pid]), float(sx[pid] / cnt[pid]), float(sy[pid] / cnt[pid])

    def link(a, b, kind):
        m.neighbours.setdefault(a, {})[b] = kind
        m.neighbours.setdefault(b, {})[a] = kind

    if src.get("rivers"):
        try:
            rv = _read_bmp_indexed(src["rivers"])
            if rv.shape == ids.shape:
                m.rivers = {k: v for k, v in _river_borders(ids, rv).items() if k[0] and k[1]}
        except ValueError as e:
            if verbose:
                print("rivers:", e)
    for (a, b), c in _pixel_graph(ids).items():
        if a and b and a in m.provinces and b in m.provinces:
            m.border[(a, b)] = c
            link(a, b, "border")
    for a, b, kind, through in _read_adjacencies(src["adjacencies"]):
        if a not in m.provinces or b not in m.provinces:
            continue
        if kind == "impassable":
            for x, y in ((a, b), (b, a)):
                m.neighbours.get(x, {}).pop(y, None)
        else:
            link(a, b, kind)
    m.states = _read_states(files)
    for s in m.states.values():
        for pid in s.provinces:
            if pid in m.provinces:
                m.provinces[pid].state = s.id
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "wb") as f:
        pickle.dump(m, f, protocol=pickle.HIGHEST_PROTOCOL)
    return m
