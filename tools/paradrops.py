"""The paradrop orders of a recording, decoded: when, which army, the airbase province and the target province.

    python paradrops.py RECORDING_DIR [--tag GER]

Reads command_record.tsv (hoi4ai.planrec.decode) and the map files of the game. A source order names the airbase
province, a target order the province to drop on (one line per target; `line` counts up per army). The drop starts
when the army is executed (`execute`/`stop` rows, shown for armies that have had a paradrop order; they can also
be the army's ordinary battle plans). Dates are the
record's clock (hoi4ai/clock.py). The paratrooper divisions of an order are listed only if the recording had the
capture rule for it (hoi4ai.planrec.RULES; recordings before 2026-11-16 do not). Whether a drop landed is not
recorded here: see occupation.tsv and unit_snapshots.tsv.
"""
import argparse
import os

import _bridge  # noqa: F401  (puts the repo on sys.path)

from hoi4ai import mapdata, planrec
from hoi4ai.clock import Clock


def drops(path):
    """[(date, owner, what, army, line, province, divisions)] in order."""
    out, clock, armies = [], Clock(), set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            date = clock.feed(line)
            p = line.rstrip("\n").split("\t")
            if len(p) < 8 or not ("Paradrop" in p[2] or p[2] == "COrderExecuteCommand"):
                continue
            d = planrec.decode(p[2], p[7], p[8] if len(p) > 8 else "")
            if not d:
                continue
            if "paradrop" in d["what"]:
                armies.add((p[3], d["army"]))
            elif (p[3], d["army"]) not in armies:       # an execute of an army with no paradrop order so far
                continue
            out.append((date, p[3], d["what"], d["army"], d.get("line", ""), d.get("province", ""), d.get("divisions", [])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--tag")
    a = ap.parse_args()
    m = mapdata.load()
    print("date\towner\twhat\tarmy\tline\tprovince\tstate\tdivisions")
    for date, owner, what, army, ln, prov, divs in drops(os.path.join(a.dir, "command_record.tsv")):
        if a.tag and owner != a.tag:
            continue
        P = m.provinces.get(prov) if prov != "" else None if prov != "" else None
        st = m.states.get(P.state) if P else None
        print("\t".join(map(str, (date, owner, what, army, ln, prov, st.name if st else "?", ",".join(map(str, divs))))))


if __name__ == "__main__":
    main()
