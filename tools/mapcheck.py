"""Build the map from the game files (through the active mods) and check it against the running game.

    python tools/mapcheck.py [PROVINCE ...]

Prints the mods in use, map size, and for each PROVINCE its neighbours. With the game running: checks every
province's state against the files, and lists who controls how many provinces.
"""
import argparse
import collections
import time

import _bridge  # noqa: F401
from hoi4ai import mapdata
from hoi4ai.gamefiles import GameFiles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("provinces", nargs="*", type=int)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()

    files = GameFiles()
    print(files.describe())
    t = time.time()
    m = mapdata.load(files, use_cache=not a.no_cache, verbose=True)
    land = sum(p.kind == "land" for p in m.provinces.values())
    print("map %dx%d: %d provinces (%d land), %d borders, %d states (%.1f s)" % (
        m.width, m.height, len(m.provinces), land, len(m.border), len(m.states), time.time() - t))
    for pid in a.provinces:
        p = m.provinces[pid]
        print(pid, p, "at (%.0f, %.0f)" % (p.x, p.y),
              {n: k for n, k in sorted(m.neighbours.get(pid, {}).items())})
    if a.no_live:
        return

    from hoi4ai.live import Provinces
    from hoi4bridge import send
    t = time.time()
    lp = Provinces()
    t1 = time.time()
    lp.refresh()
    t2 = time.time()
    lp.refresh()
    t3 = time.time()
    print("live: %d provinces, table found in %.2f s, first read %.2f s, refresh %.1f ms" % (
        lp.count - 1, t1 - t, t2 - t1, (t3 - t2) * 1000))
    bad = [(pid, p.state, int(lp.state[pid])) for pid, p in m.provinces.items()
           if p.kind == "land" and pid < lp.count and p.state and lp.state[pid] != p.state]
    print("state check: %d land provinces disagree with the files%s" % (len(bad), (" e.g. %s" % bad[:5]) if bad else ""))
    ids = collections.Counter(int(c) for c in lp.controller[1:] if c)
    tags = {}
    for i in range(0, len(ids), 60):
        chunk = sorted(ids)[i:i + 60]
        for kv in send("bridge.tag " + " ".join(map(str, chunk)))[0][3:].split():
            k, v = kv.split("=")
            tags[int(k)] = v
    occupied = sum(1 for i in range(1, lp.count) if lp.controller[i] and lp.controller[i] != lp.owner[i])
    print("controllers:", ", ".join("%s %d" % (tags.get(c, c), n) for c, n in ids.most_common(12)),
          "| provinces controlled by someone other than the owner:", occupied)


if __name__ == "__main__":
    main()
