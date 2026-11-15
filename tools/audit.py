"""Soundness check of a cmdrec recording: is the data complete and consistent enough to learn from?

    python audit.py RECORDING_DIR [--tag NGR] [--json FILE]

Run it on every new recording before analysing it. It reads every file of the folder once and reports:
  record     the in-game span of command_record.tsv, real time per in-game day (by quarter), save reloads (the date
             going back), hours with no tick (the clock jumping), the player's commands by class and the ones the
             record has no owner for, and how many of those unit_snapshots credits to a country
  snapshots  missing days, the player's divisions per day (both handles) and sudden drops
  logs       per log: rows, the player's rows, first and last date, months with no row while the run went on,
             and how far the log ends before the record (a log that falls behind or goes quiet)
  country    country.tsv: missing days, columns that never change, and the country/DAY.txt.gz files
  examples   examples.jsonl per source and per quarter, and examples with an empty state
Problems are listed at the end as WARN lines. --json writes the numbers for later tools (a report page).
"""
import argparse
import datetime
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from examples import COUNTRY_AT_28, LOGS, NOISE, division, player_of, rows, when  # noqa: E402

# unowned commands the examples get an owner for another way: events (events.tsv), country commands (+0x28)
HANDLED = set(COUNTRY_AT_28) | {"CSelectEventOptionCommand"}
from hoi4ai.clock import Clock  # noqa: E402

DAY = datetime.timedelta(days=1)


def quarter(d):
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def month(d):
    return f"{d.year}-{d.month:02d}"


def months_between(a, b):
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def snapshot_owners(path):
    """({division key: owner}, {day: Counter(owner)}, [days in file order])."""
    owners, per_day, days, handle = {}, defaultdict(Counter), [], "51"
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split("\t", 3)
            if p[0] == "#snap":
                day, handle = p[1], p[2]
                if not days or days[-1] != day:
                    days.append(day)
            elif len(p) > 2 and p[0].isdigit():
                owners[division(int(handle), int(p[0]))] = p[1]
                per_day[day][p[1]] += 1
    return owners, per_day, days


def audit_record(path, tag, owners):
    from examples import owner_of
    clock = Clock()
    r = {"player_classes": Counter(), "unowned": Counter(), "unowned_player": Counter(), "unowned_other": Counter(),
         "unowned_none": Counter(), "reloads": [], "tick_gaps": 0, "real_per_quarter": {}, "first": None, "last": None}
    last_mark, first_ms, prev = None, None, None
    q_ms = {}       # quarter -> [first ms, last ms, first date, last date]
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#mark" and len(p) > 2 and p[2].startswith("date="):
                d = when(p[2][5:])
                if d and last_mark and d < last_mark - DAY:
                    r["reloads"].append((last_mark.isoformat(" "), d.isoformat(" ")))
                last_mark = d or last_mark
            date = clock.feed(line)
            if p[0].startswith("#") or len(p) < 8:
                continue
            d = when(date) if date else None
            if d is None:
                continue
            r["first"] = r["first"] or d
            r["last"] = d
            if p[2] == "CHourlyTickCommand":
                ms = int(p[1])
                q = quarter(d)
                e = q_ms.setdefault(q, [ms, ms, d, d])
                e[1], e[3] = ms, d
                continue
            if p[2] in NOISE:
                continue
            if p[3] == tag:
                r["player_classes"][p[2]] += 1
            elif p[3] == "-":
                r["unowned"][p[2]] += 1
                o = owner_of(p[2], p[6], p[7], p[8] if len(p) > 8 else "", owners)
                key = "unowned_player" if o == tag else "unowned_other" if o else "unowned_none"
                r[key][p[2]] += 1
    for q, (a, b, da, db) in sorted(q_ms.items()):
        days = (db - da).total_seconds() / 86400
        r["real_per_quarter"][q] = round((b - a) / 1000 / days, 2) if days > 1 and b > a else None
    return r


def audit_logs(folder, tag, start, end):
    out = {}
    for src, cols in list(LOGS.items()) + [("plans", ("army",))]:
        path = os.path.join(folder, src + ".tsv")
        if not os.path.exists(path):
            out[src] = {"missing": True}
            continue
        n, mine, first, last, per_month, changes, reads = 0, 0, None, None, Counter(), Counter(), set()
        for row in rows(path):
            d = when(row.get("date", ""))
            if d is None:
                continue
            n += 1
            reads.add(d)
            is_mine = src == "plans" or any(row.get(c) == tag for c in cols if c)
            if not is_mine:
                continue
            mine += 1
            changes[row.get("change", "")] += 1
            if row.get("change") == "start":
                continue
            first, last = first or d, d
            per_month[month(d)] += 1
        quiet = [m for m in months_between(start, end) if not per_month[m]] if first else []
        gaps = defaultdict(list)    # quarter -> hours between reads (a polled log is dated when it is read)
        reads = sorted(reads)
        for x, y in zip(reads, reads[1:]):
            gaps[quarter(x)].append((y - x).total_seconds() / 3600)
        out[src] = {"rows": n, "player_rows": mine, "changes": dict(changes),
                    "first": first and first.isoformat(" "), "last": last and last.isoformat(" "),
                    "ends_before_record_days": last and round((end - last).total_seconds() / 86400, 1),
                    "quiet_months": quiet, "per_month": dict(per_month),
                    "read_hours": {q: sorted(v)[len(v) // 2] for q, v in sorted(gaps.items())}}
    return out


def audit_country(folder):
    path = os.path.join(folder, "country.tsv")
    if not os.path.exists(path):
        return {"missing": True}
    days, values = [], defaultdict(set)
    for row in rows(path):
        d = when(row["date"])
        if d:
            days.append(d)
        for k, v in row.items():
            if k != "date":
                values[k].add(v)
    steps = Counter(round((b - a).total_seconds() / 86400) for a, b in zip(days, days[1:]))
    cadence = steps.most_common(1)[0][0] if steps else None
    gaps = [(a.date().isoformat(), b.date().isoformat()) for a, b in zip(days, days[1:])
            if cadence and (b - a).days > cadence * 2]
    gz_dir = os.path.join(folder, "country")
    gz = sorted(os.listdir(gz_dir)) if os.path.isdir(gz_dir) else []
    tsv_days = {d.date().isoformat() for d in days}
    gz_days = {g.split(".")[0] for g in gz}
    bad = []
    for g in gz[:: max(1, len(gz) // 8)] + gz[-1:]:
        try:
            with gzip.open(os.path.join(gz_dir, g), "rt", encoding="utf-8", errors="replace") as f:
                if len(f.read(200)) < 50:
                    bad.append(g)
        except (OSError, EOFError):
            bad.append(g)
    return {"days": len(days), "cadence_days": cadence, "steps": dict(steps), "gaps": gaps,
            "constant_columns": sorted(k for k, v in values.items() if len(v) == 1),
            "dump_files": len(gz), "tsv_days_without_dump": len(tsv_days - gz_days),
            "dumps_without_tsv_day": len(gz_days - tsv_days), "unreadable_dumps": bad}


def audit_snapshots(per_day, days, tag):
    ds = [datetime.date.fromisoformat(d) for d in days]
    missing = []
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > 1:
            missing.append((a.isoformat(), b.isoformat(), (b - a).days - 1))
    mine = [(d, per_day[d][tag]) for d in days]
    drops = [(d0, n0, d1, n1) for (d0, n0), (d1, n1) in zip(mine, mine[1:]) if n0 >= 10 and n1 < n0 * 0.7]
    return {"days": len(days), "first": days[0] if days else None, "last": days[-1] if days else None,
            "missing": missing, "player_divisions": {d: n for d, n in mine[:: max(1, len(mine) // 40)] + mine[-1:]},
            "drops": drops, "out_of_order": sum(1 for a, b in zip(ds, ds[1:]) if b <= a)}


def audit_examples(path):
    if not os.path.exists(path):
        return {"missing": True}
    by_src, by_q, empty, n, auto = Counter(), Counter(), 0, 0, 0
    timed, shifts = Counter(), defaultdict(list)    # log rows dated by their command, and by how much
    with open(path, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            n += 1
            auto += bool(e.get("auto"))
            by_src[e["source"]] += 1
            d = when(e["date"])
            if d:
                by_q[quarter(d)] += 1
            if e.get("logged") and d:
                timed[e["source"]] += 1
                shifts[e["source"]].append((when(e["logged"]) - d).total_seconds() / 3600)
            if not e.get("state", {}).get("country"):
                empty += 1
    return {"examples": n, "auto": auto, "by_source": dict(by_src.most_common()), "by_quarter": dict(sorted(by_q.items())),
            "empty_state": empty, "dated_by_command": dict(timed),
            "shift_hours": {k: {"median": sorted(v)[len(v) // 2], "max": max(v)} for k, v in shifts.items()}}


def warnings(rep):
    w = []
    rec, snaps, logs, cty = rep["record"], rep["snapshots"], rep["logs"], rep["country"]
    for a, b in rec["reloads"]:
        w.append(f"record: date went back from {a} to {b} (save reload: commands after it replay the same days)")
    lost = Counter({c: n for c, n in rec["unowned_none"].items() if c not in HANDLED})
    if lost:
        top = ", ".join(f"{c} {n}" for c, n in lost.most_common(5))
        w.append(f"record: {sum(lost.values())} commands with no owner the examples can find ({top}); "
                 "the player's may hide among them")
    for a, b, n in snaps["missing"]:
        w.append(f"snapshots: {n} day(s) missing between {a} and {b}")
    for d0, n0, d1, n1 in snaps["drops"]:
        w.append(f"snapshots: player divisions {n0} on {d0} -> {n1} on {d1}")
    for src, l in logs.items():
        if l.get("missing"):
            w.append(f"logs: {src}.tsv missing")
            continue
        if l["ends_before_record_days"] and l["ends_before_record_days"] > 30 and l["player_rows"] > 50:
            w.append(f"logs: {src} ends {l['ends_before_record_days']} days before the record")
        busy = [n for n in l["per_month"].values()]
        if l["quiet_months"] and busy and sum(busy) / len(busy) >= 20:
            w.append(f"logs: {src} has no player row in {', '.join(l['quiet_months'][:6])}"
                     f"{' ...' if len(l['quiet_months']) > 6 else ''} (averages {sum(busy) // len(busy)}/month)")
    if cty.get("gaps"):
        a, b = max(cty["gaps"], key=lambda g: datetime.date.fromisoformat(g[1]) - datetime.date.fromisoformat(g[0]))
        w.append(f"country: {len(cty['gaps'])} gaps longer than twice the {cty['cadence_days']}-day cadence "
                 f"(the longest {a} .. {b}): the logs' reads fell behind")
    busiest = max((l for l in logs.values() if not l.get("missing")), key=lambda l: l["rows"], default={})
    slow = {q: h for q, h in busiest.get("read_hours", {}).items() if h > 48}
    if slow:
        w.append("logs: read every " + ", ".join(f"{q} {h / 24:.0f}d" for q, h in sorted(slow.items()))
                 + " (median): their rows are dated by the read, not the change; examples.py re-dates the ones"
                 " it can match to a command")
    if cty.get("unreadable_dumps"):
        w.append(f"country: unreadable dumps {cty['unreadable_dumps']}")
    if rep["examples"].get("empty_state", 0) > 5:
        w.append(f"examples: {rep['examples']['empty_state']} with an empty country state")
    return w


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dir")
    ap.add_argument("--tag")
    ap.add_argument("--json")
    a = ap.parse_args()
    rec_path = os.path.join(a.dir, "command_record.tsv")
    tag = a.tag or player_of(rec_path)
    if not tag:
        sys.exit("no player tag: pass --tag")
    print(f"player {tag}", flush=True)
    owners, per_day, days = snapshot_owners(os.path.join(a.dir, "unit_snapshots.tsv"))
    print("snapshots read", flush=True)
    rec = audit_record(rec_path, tag, owners)
    print("record read", flush=True)
    rep = {"dir": a.dir, "tag": tag, "record": rec, "snapshots": audit_snapshots(per_day, days, tag),
           "logs": audit_logs(a.dir, tag, rec["first"], rec["last"]), "country": audit_country(a.dir),
           "examples": audit_examples(os.path.join(a.dir, "examples.jsonl"))}
    rec["first"], rec["last"] = rec["first"].isoformat(" "), rec["last"].isoformat(" ")

    print(f"\n== record {rec['first']} .. {rec['last']}")
    print("real seconds per in-game day:", "  ".join(f"{q} {v}" for q, v in rec["real_per_quarter"].items()))
    print(f"reloads: {len(rec['reloads'])}")
    print(f"player commands: {sum(rec['player_classes'].values())}")
    for c, n in rec["player_classes"].most_common(15):
        print(f"  {n:7d} {c}")
    print(f"unowned commands: {sum(rec['unowned'].values())}  -> player {sum(rec['unowned_player'].values())}, "
          f"other countries {sum(rec['unowned_other'].values())}, nobody {sum(rec['unowned_none'].values())}")
    for c, n in rec["unowned"].most_common(10):
        print(f"  {n:7d} {c}  (player {rec['unowned_player'][c]}, other {rec['unowned_other'][c]}, nobody {rec['unowned_none'][c]})")
    s = rep["snapshots"]
    print(f"\n== snapshots {s['first']} .. {s['last']}: {s['days']} days, {len(s['missing'])} gaps")
    print("player divisions:", "  ".join(f"{d[2:7]} {n}" for d, n in s["player_divisions"].items()))
    big = max(rep["logs"].values(), key=lambda l: l.get("rows", 0))
    print("\n== logs read every (median hours, the busiest log):",
          "  ".join(f"{q} {h:.0f}" for q, h in big.get("read_hours", {}).items()))
    print("== logs (player rows, not counting start)")
    for src, l in sorted(rep["logs"].items(), key=lambda x: -x[1].get("player_rows", 0)):
        if l.get("missing"):
            print(f"  {src:20s} missing")
            continue
        print(f"  {src:20s} {l['player_rows']:7d}/{l['rows']:7d}  {l['first'] or '-':19s} .. {l['last'] or '-':19s}"
              f"  quiet months {len(l['quiet_months'])}")
    c = rep["country"]
    print(f"\n== country: {c.get('days')} rows every {c.get('cadence_days')} days, {c.get('dump_files')} dumps, "
          f"constant columns: {', '.join(c.get('constant_columns', [])) or '-'}")
    e = rep["examples"]
    print(f"\n== examples: {e.get('examples')} ({e.get('auto')} auto)")
    print("  " + "  ".join(f"{k} {v}" for k, v in e.get("by_source", {}).items()))
    print("  " + "  ".join(f"{k} {v}" for k, v in e.get("by_quarter", {}).items()))
    print("  dated by their command (log rows; hours earlier than the log, median / max): " + "  ".join(
        f"{k} {n} ({e['shift_hours'][k]['median']:.0f}/{e['shift_hours'][k]['max']:.0f})"
        for k, n in e.get("dated_by_command", {}).items()))
    rep["warnings"] = warnings(rep)
    print()
    for w in rep["warnings"]:
        print("WARN", w)
    if not rep["warnings"]:
        print("no problems found")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, default=str)


if __name__ == "__main__":
    main()
