"""The paradrop orders of a recording, decoded: when, which army, the airbase province and the target province.

    python paradrops.py RECORDING_DIR [--tag GER] [--landed]

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


HEADER = "date\towner\tarmy\tline\tsource\ttarget\tstate\tlanded\tlanded_on\tdivisions"
MAX_GAP = 2        # days between two snapshots of a division for a move to count (the recorder skips days)


def landings(rec, tag=None):
    """[(date, owner, army, line, source, target, landed, landed_on, divisions)]: one row per paradrop line (the last
    target order of each line wins), `landed` yes or no. A landing is a division of the order's army (snapshot column
    ib0.c) standing in the source province on one snapshot and in the target province on the next (at most MAX_GAP
    days later), on or after the order's day; each such move is credited to the latest order of that source and target
    that was set before it, so a target used again is not counted twice. `landed_on` is the snapshot day of the first
    landing, `divisions` how many arrived by then. A drop that fell between two snapshots, or a drop on a day the
    recorder skipped for longer than MAX_GAP, shows as no."""
    import datetime
    src, lines = {}, {}
    for date, owner, what, army, line, prov, divs in drops(os.path.join(rec, "command_record.tsv")):
        if tag and owner != tag:
            continue
        if what == "paradrop_source":
            src[(owner, army)] = prov
        elif what == "paradrop_target":
            lines[(owner, army, line)] = (date, src.get((owner, army), ""), prov)     # the last target of a line wins
    pos, cur = {}, None            # day -> {(army, division): province}
    with open(os.path.join(rec, "unit_snapshots.tsv"), encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if p[0] == "#snap":
                cur = pos.setdefault(p[1], {})
                handle = p[2]
            elif cur is not None and len(p) > 4 and p[2].isdigit() and p[3].isdigit():
                cur[(int(p[3]), handle + ":" + p[0])] = int(p[2])
    days = sorted(pos)
    ordered = sorted(lines.items(), key=lambda kv: parse_date(kv[1][0]))
    won = {}                       # order key -> [snapshot day, divisions]
    for a, b in zip(days, days[1:]):
        gap = (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
        if gap > MAX_GAP:
            continue
        for (army, div), to in pos[b].items():
            frm = pos[a].get((army, div))
            if frm is None or frm == to:
                continue
            best = None
            for (owner, arm, line), (date, s, t) in ordered:
                if arm == army and (s, t) == (frm, to) and parse_date(date) <= datetime.date.fromisoformat(b):
                    best = (owner, arm, line)
            if best is not None:
                w = won.setdefault(best, [b, 0])
                if w[0] == b:
                    w[1] += 1
    out = []
    for key, (date, s, t) in ordered:
        w = won.get(key)
        out.append((date, key[0], key[1], key[2], s, t, "yes" if w else "no", w[0] if w else "", w[1] if w else 0))
    return out


def parse_date(text):
    from hoi4ai.clock import parse
    return parse(text)[0].date()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--tag")
    ap.add_argument("--landed", action="store_true", help="write DIR/paradrops.tsv: each paradrop line, did it land")
    a = ap.parse_args()
    m = mapdata.load()
    if a.landed:
        rows = landings(a.dir, a.tag)
        path = os.path.join(a.dir, "paradrops.tsv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(HEADER + "\n")
            for date, owner, army, line, s, t, yes, on, n in rows:
                P = m.provinces.get(int(t)) if str(t).isdigit() else None
                st = m.states.get(P.state) if P else None
                f.write("\t".join(map(str, (date, owner, army, line, s, t, st.name if st else "?", yes, on, n))) + "\n")
        print(f"{len(rows)} lines, {sum(r[6] == 'yes' for r in rows)} landed; wrote {path}")
        return
    print("date\towner\twhat\tarmy\tline\tprovince\tstate\tdivisions")
    for date, owner, what, army, ln, prov, divs in drops(os.path.join(a.dir, "command_record.tsv")):
        if a.tag and owner != a.tag:
            continue
        P = m.provinces.get(prov) if prov != "" else None
        st = m.states.get(P.state) if P else None
        print("\t".join(map(str, (date, owner, what, army, ln, prov, st.name if st else "?", ",".join(map(str, divs))))))


if __name__ == "__main__":
    main()
