"""A country's division templates in the running game and how they fight (see hoi4ai/templates.py).

    python tools/templates.py [TAG] [--json OUT]

Per template: how many divisions use it, its battalions and support companies, the equipment its divisions hold,
soft and hard attack, speed and combat width (averages over its divisions, with the range when divisions differ),
and its terrain modifiers (attack / movement, where any).
"""
import argparse
import json
import os
import sys

import _bridge  # noqa: F401
from controller import REPO
from hoi4ai import templates
from hoi4ai.divisions import Snapshotter
from hoi4ai.memory import Process


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", nargs="?", default="GER")
    ap.add_argument("--json")
    a = ap.parse_args()
    divs = Snapshotter(os.path.join(REPO, ".cache", "templates_snap.tsv")).take()
    out = templates.country(a.tag, divs, Process())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rng = lambda t, k, f: f"{t[k]:{f}}" + ("" if t[k + "_range"][1] - t[k + "_range"][0] < 0.5 else
                                          f" ({t[k + '_range'][0]:.0f}-{t[k + '_range'][1]:.0f})")
    for name, t in sorted(out.items(), key=lambda x: -x[1]["count"]):
        bats = ", ".join(f"{n} {u}" for u, n in t["battalions"].items())
        sup = ", ".join(t["support"]) or "no support"
        print(f"{t['count']:3}x {name}: {bats} | {sup}")
        print(f"      soft {rng(t, 'soft_attack', '.0f')}  hard {rng(t, 'hard_attack', '.0f')}  "
              f"speed {t['maximum_speed']:.1f} km/h  width {t['combat_width']:.0f}")
        terr = []
        for ter, m in sorted(t["terrain"].items()):
            parts = [f"{k[:3]} {v:+.0%}" for k, v in sorted(m.items()) if k != "defence" and abs(v) >= 0.005]
            if parts:
                terr.append(f"{ter} " + " ".join(parts))
        if terr:
            print("      terrain: " + "; ".join(terr))
        print("      equipment: " + ", ".join(f"{k} {v:.0f}" for k, v in t["equipment"].most_common()))
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), indent=1, default=dict)
        print("wrote", a.json)


if __name__ == "__main__":
    main()
