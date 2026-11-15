"""The forces of one country over a recording: division templates, divisions, factories and the air force.

    python tools/orbat.py RECORDING_DIR TAG [--json FILE] [--step 3]

Works for any country (the AI's or the player's) from the logs the recorder writes for everyone:
division_templates.tsv, unit_snapshots.tsv, production_lines.tsv, air.tsv and the equipment designs. Prints
markdown: the templates as they changed, then a table every STEP months of divisions per template, factories per
kind of equipment, and air wings and planes per role and per aircraft model. --json also writes the same numbers.

Air roles come from the airframe of the wing's equipment (small_plane = fighter, ..._cas = CAS, medium = tactical
bomber, large = strategic bomber, naval_bomber, cv_ = carrier), not from the wing's name.
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from examples import rows, when  # noqa: E402
from playstyle import production  # noqa: E402


def role(t):
    """small_plane_cas_airframe_1 -> CAS; cv_small_plane_airframe_1 -> carrier fighter."""
    t = t or ""
    cv = "carrier " if t.startswith("cv_") else ""
    for key, name in (("naval_bomber", "naval bomber"), ("cas", "CAS"), ("interceptor", "fighter"),
                      ("small_plane", "fighter"), ("medium_plane", "tactical bomber"),
                      ("large_plane", "strategic bomber"), ("heavy_fighter", "fighter"),
                      ("transport", "transport"), ("scout", "recon"), ("airship", "airship")):
        if key in t:
            return cv + name
    return "other"


def designs(folder):
    """{design id: (name, type)} from equipment_designs.tsv, later rows (refits) winning."""
    out = {}
    path = os.path.join(folder, "equipment_designs.tsv")
    if os.path.exists(path):
        for r in rows(path):
            out[r["id"]] = (r.get("name") or r.get("type"), r.get("type"))
    return out


def quarters(first, last, step):
    d, out = datetime.date(first.year, 1, 1), []
    while d <= last:
        out.append(d)
        m = d.month - 1 + step
        d = datetime.date(d.year + m // 12, m % 12 + 1, 1)
    return out + [last]


def templates(folder, tag):
    out = []
    for r in rows(os.path.join(folder, "division_templates.tsv")):
        if r["owner"] == tag and r["name"]:
            groups = [g.split(",") for g in r["battalions"].split("|") if g]
            out.append({"date": when(r["date"]).date().isoformat(), "change": r["change"], "name": r["name"],
                        "battalions": Counter(b for g in groups for b in g), "support": r["support"]})
    return out


def divisions(folder, tag):
    """{day: Counter(template name)} from the daily snapshots."""
    out, day = {}, None
    with open(os.path.join(folder, "unit_snapshots.tsv"), encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#snap":
                day = p[1]
                out.setdefault(day, Counter())
            elif day and len(p) > 3 and p[1] == tag:
                name = re.sub(r"\s*Division$", "", re.sub(r"^\d+\.?\s*", "", p[-1])).strip()
                out[day][name or "?"] += 1
    return out


def air(folder, tag, names):
    """{date: {wing: (equipment id, planes)}} after each change to TAG's wings, as a list of (date, wings)."""
    wings, log = {}, []
    path = os.path.join(folder, "air.tsv")
    if not os.path.exists(path):
        return log
    for r in rows(path):
        if r["country"] != tag:
            continue
        if r["change"] == "removed":
            wings.pop(r["wing"], None)
        else:
            wings[r["wing"]] = (r["equipment"], int(r["planes"] or 0))
        log.append((when(r["date"]).date(), dict(wings)))
    return log


def at(log, day):
    last = log[0][1]    # the wings start a couple of days into the record; use them for the days before
    for d, w in log:
        if d > day:
            break
        last = w
    return last


def table(head, lines):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(map(str, l)) + " |" for l in lines]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("tag")
    ap.add_argument("--json")
    ap.add_argument("--step", type=int, default=3, help="months between table rows (default 3)")
    a = ap.parse_args()
    tag = a.tag.upper()
    ds = designs(a.folder)
    divs = divisions(a.folder, tag)
    if not any(divs.values()):
        sys.exit(f"{tag} has no divisions in {a.folder}/unit_snapshots.tsv")
    days = sorted(divs)
    first, last = datetime.date.fromisoformat(days[0]), datetime.date.fromisoformat(days[-1])
    qs = quarters(first, last, a.step)
    result = {"tag": tag, "templates": [], "divisions": {}, "factories": {}, "air": {}}
    print(f"# {tag}, {first} to {last}\n\n## Division templates\n")
    for t in templates(a.folder, tag):
        bat = " ".join(f"{n}x{b}" for b, n in t["battalions"].most_common())
        print(f"- {t['date']} {t['change']} **{t['name']}**: {bat}" + (f" + {t['support']}" if t["support"] else ""))
        result["templates"].append({**t, "battalions": dict(t["battalions"])})

    def nearest(day):
        return divs[min(days, key=lambda d: abs((datetime.date.fromisoformat(d) - day).days))]

    seen = Counter()
    for q in qs:
        seen.update(nearest(q))
    top = [n for n, _ in seen.most_common(8)]
    rowsd = []
    for q in qs:
        c = nearest(q)
        rowsd.append([q, sum(c.values())] + [c.get(n, 0) for n in top] + [sum(c.values()) - sum(c.get(n, 0) for n in top)])
        result["divisions"][q.isoformat()] = dict(c)
    print("\n## Divisions per template\n")
    print(table(["date", "total"] + top + ["other"], rowsd))

    pr = production(a.folder, tag, datetime.datetime.combine(last, datetime.time()))
    kinds = Counter()
    for m in pr.values():
        kinds.update(m)
    ktop = [k for k, _ in kinds.most_common(10)]
    rowsp = []
    for q in qs:
        m = pr.get(f"{q.year}-{q.month:02d}") or pr.get(max(pr, default=""), {}) or {}
        rowsp.append([q, round(sum(m.values()))] + [round(m.get(k, 0)) for k in ktop])
        result["factories"][q.isoformat()] = m
    print("\n## Factories on production lines, per kind of equipment\n")
    print(table(["date", "total"] + ktop, rowsp))

    log = air(a.folder, tag, ds)
    if log:
        roles, models = Counter(), Counter()
        snaps = []
        for q in qs:
            w = at(log, q)
            rc, mc = Counter(), Counter()
            for eq, n in w.values():
                name, typ = ds.get(eq, (eq, ""))
                rc[role(typ)] += n
                mc[name] += n
            snaps.append((q, len(w), rc, mc))
            roles.update(rc)
            models.update(mc)
        rtop = [r for r, _ in roles.most_common(8)]
        print("\n## Air force: planes per role\n")
        print(table(["date", "wings", "planes"] + rtop,
                    [[q, n, sum(rc.values())] + [rc.get(r, 0) for r in rtop] for q, n, rc, _ in snaps]))
        mtop = [m for m, _ in models.most_common(8)]
        print("\n## Air force: planes per aircraft model\n")
        print(table(["date"] + mtop, [[q] + [mc.get(m, 0) for m in mtop] for q, _, _, mc in snaps]))
        for q, n, rc, mc in snaps:
            result["air"][q.isoformat()] = {"wings": n, "roles": dict(rc), "models": dict(mc)}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)
        print(f"\nwrote {a.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
