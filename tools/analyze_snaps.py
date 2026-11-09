"""Daily division snapshots (unit_snapshots.tsv, written by cmdrec.py) -> how each country's army fared.

    python analyze_snaps.py [SNAPSHOTS] [--countries GER POL ...] [--every 3]

Per country and day: divisions, mean strength, mean organisation (as a share of the highest seen for that division), divisions that moved
since the last snapshot, divisions under a front or battle plan, and divisions lost since the first snapshot.
"""
import argparse
import os
from collections import defaultdict

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"


def load(path):
    snaps = []   # [(day, {id: row})]
    cols = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#snap":
                snaps.append((p[1], {}))
                cols = [c for c in p[3:] if not c.startswith("class=")]
                continue
            if not snaps or len(p) < len(cols) + 1:
                continue
            row = dict(zip(cols, p[1:]))
            snaps[-1][1][int(p[0])] = {
                "owner": row["c1c8"], "province": int(row["i1e0.a4"]), "order": int(row["ib0.c"]),
                "org": [int(row[k]) for k in ("i20", "i28", "i78")], "strength": int(row["i30"]) / 1000,
                "template": row.get("s3a8.20", "")}
    return snaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", nargs="?", default=os.path.join(GAME, "unit_snapshots.tsv"))
    ap.add_argument("--countries", nargs="*", default=["GER", "POL"])
    ap.add_argument("--every", type=int, default=1, help="show every Nth day")
    a = ap.parse_args()

    snaps = load(a.snapshots)
    if not snaps:
        print("no snapshots")
        return
    print(f"{len(snaps)} daily snapshots, {snaps[0][0]} .. {snaps[-1][0]}")
    # The org fields hold current organisation; its maximum isn't stored with them, so take the highest seen.
    # Divisions with no strength and no organisation (at sea, still deploying) are left out.
    top = defaultdict(int)
    for _, units in snaps:
        for i, u in list(units.items()):
            if u["strength"] == 0 and max(u["org"]) == 0:
                del units[i]
                continue
            top[i] = max(top[i], u["org"][0])

    first = {c: {i for i, u in snaps[0][1].items() if u["owner"] == c} for c in a.countries}
    for c in a.countries:
        print(f"\n{c}:  day         divs  strength  org     moved  in-order  lost")
        prev = None
        for n, (day, units) in enumerate(snaps):
            mine = {i: u for i, u in units.items() if u["owner"] == c}
            if not mine:
                continue
            strength = sum(u["strength"] for u in mine.values()) / len(mine)
            org = sum(u["org"][0] / max(top[i], 1) for i, u in mine.items()) / len(mine) * 100
            moved = sum(1 for i, u in mine.items() if prev and i in prev and prev[i]["province"] != u["province"])
            in_order = sum(1 for u in mine.values() if u["order"])
            lost = len(first[c] - set(mine))
            if n % a.every == 0 or n == len(snaps) - 1:
                print(f"      {day}  {len(mine):5}  {strength:6.1f}%  {org:5.1f}%  {moved:5}  {in_order:8}  {lost:4}")
            prev = mine


if __name__ == "__main__":
    main()
