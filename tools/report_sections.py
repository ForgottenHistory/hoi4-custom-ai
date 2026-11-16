"""The log-based sections of report.py: occupation and resistance, navy, air force, commanders, trade, intelligence.

Each section replays one recorder log (occupation.tsv, naval.tsv, air.tsv, generals.tsv, admirals.tsv, trade.tsv,
intelligence.tsv) for the player's country and returns (title, html). Rows are read by column name through rows.py
(`final_rows` drops the stretch a reload abandoned). A section whose file is missing is left out.
"""
import datetime
import os
import re
from collections import Counter

from rows import final_rows, load


def replay(rows, header, keycols, sample):
    """Replay start/new/changed/removed rows keyed by KEYCOLS; {(year, month): SAMPLE(state) at the month's last row}."""
    from report import day
    ix = {c: i for i, c in enumerate(header)}
    state, out = {}, {}
    for r in rows:
        d = day(r[0])
        if d is None:
            continue
        k = tuple(r[ix[c]] for c in keycols)
        if r[ix["change"]] == "removed":
            state.pop(k, None)
        else:
            state[k] = dict(zip(header, r))
        out[(d.year, d.month)] = sample(state)
    return out


def stack_series(by_month, names, d0, d1):
    """[(name, [(date, value)])] for every month of the span, carrying the last sample forward."""
    cols, last = {n: [] for n in names}, None
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        last = by_month.get((y, m), last) or {}
        for n in names:
            cols[n].append((datetime.date(y, m, 15), last.get(n, 0)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return list(cols.items())


def rows_of(rec, name, **filt):
    path = os.path.join(rec, name + ".tsv")
    if not os.path.exists(path):
        return None, []
    header, rows = load(path)
    ix = {c: i for i, c in enumerate(header)}
    return header, [r for r in final_rows(rows) if all(r[ix[k]] == v for k, v in filt.items())]


def top_names(by_month):
    peak = Counter()
    for v in by_month.values():
        for k, n in v.items():
            peak[k] = max(peak[k], n)
    return [k for k, _ in peak.most_common()]


def sections(rec, tag, d0, d1, wars):
    from report import LOC, chart, day, esc, nm, table
    out = []

    # occupation: each row of the resistance attack log is the newest attack at the time of the read
    h, occ = rows_of(rec, "occupation", country=tag, key="resistance_attack_log")
    if occ:
        v = h.index("value")
        seen, hits, lost, per = set(), Counter(), Counter(), Counter()
        for r in occ:
            c, dt = re.search(r"country=(\w+)", r[v]), re.search(r"date=(\d+)\.(\d+)\.(\d+)", r[v])
            m = re.search(r"manpower=\{value=\{tag=\w+ value=(\d+)", r[v])
            if not (c and dt) or (c.group(1), dt.group(0)) in seen:
                continue
            seen.add((c.group(1), dt.group(0)))
            hits[c.group(1)] += 1
            lost[c.group(1)] += int(m.group(1)) if m else 0
            per[(int(dt.group(1)), int(dt.group(2)))] += 1
        out.append(("Occupation and resistance",
                    f"<p>{sum(hits.values())} distinct resistance attacks on {esc(tag)}'s garrisons are in the log. The log keeps only "
                    "the newest attack at each read, so attacks between two reads are not counted (an undercount). Manpower is what the "
                    "log states the attack killed. The recorder wrote no per-state occupation laws for "
                    f"{esc(tag)}, only its default law and garrison template.</p>" +
                    table(["occupied country", "attacks", "manpower lost"], [(nm(c), n, lost[c]) for c, n in hits.most_common(12)]) +
                    "<h3>Attacks per month</h3>" +
                    chart([("attacks", [(datetime.date(y, m, 15), n) for (y, m), n in sorted(per.items())])], d0, d1, wars, bars=True, height=150)))

    h, nav = rows_of(rec, "naval", country=tag)
    if nav:
        def ships(state):
            c = Counter()
            for row in state.values():
                for part in (row["ships"] or "").split(","):
                    k, _, n = part.rpartition(":")
                    if k and n.isdigit():
                        c[k] += int(n)
            return c
        by = replay(nav, h, ["task_force"], ships)
        out.append(("Navy", "<p>Ships by type in the task forces, replayed from naval.tsv (ships not in a task force are not listed).</p>" +
                    chart(stack_series(by, top_names(by), d0, d1), d0, d1, wars, stacked=True)))

    h, air = rows_of(rec, "air", country=tag)
    if air:
        by = replay(air, h, ["wing"], lambda st: Counter({"planes": sum(int(r["planes"] or 0) for r in st.values()), "wings": len(st)}))
        out.append(("Air force", "<p>Planes and wings, replayed from air.tsv.</p><h3>Planes</h3>" +
                    chart(stack_series(by, ["planes"], d0, d1), d0, d1, wars, height=170) +
                    "<h3>Wings</h3>" + chart(stack_series(by, ["wings"], d0, d1), d0, d1, wars, height=130)))

    h, gen = rows_of(rec, "generals", country=tag)
    if gen:
        by = replay(gen, h, ["leader"], lambda st: Counter(r["role"] for r in st.values()))
        final = {}
        for r in gen:
            k = r[h.index("leader")]
            if r[h.index("change")] == "removed":
                final.pop(k, None)
            else:
                final[k] = dict(zip(h, r))
        top = sorted(final.values(), key=lambda r: -sum(int(r[c] or 0) for c in ("attack", "defense", "planning", "logistics")))[:12]
        ah, adm = rows_of(rec, "admirals", country=tag)
        out.append(("Generals and admirals", "<h3>Commanders over time</h3>" +
                    chart(stack_series(by, top_names(by), d0, d1), d0, d1, wars, stacked=True, height=170) +
                    "<h3>Best commanders at the end (attack + defense + planning + logistics)</h3>" +
                    table(["name", "role", "att", "def", "plan", "log", "traits"],
                          [(esc(LOC(r["name"])), esc(r["role"]), r["attack"], r["defense"], r["planning"], r["logistics"],
                            esc(", ".join(LOC(x) for x in (r["traits"] or "").split(",") if x))) for r in top]) +
                    (f"<p class='small'>{len({r[ah.index('admiral')] for r in adm})} admirals appear in admirals.tsv.</p>" if adm else "")))

    h, tr = rows_of(rec, "trade")
    if tr:
        ix = {c: i for i, c in enumerate(h)}
        deals = [r for r in tr if r[ix["deal"]] not in ("", "setting")]

        def amount(st):
            c = Counter()
            for row in st.values():
                try:
                    c[row["resource"]] += float(row["amount"])
                except ValueError:
                    pass
            return c
        body = f"<p>Resources per day in {esc(tag)}'s trade deals, replayed from trade.tsv by deal id.</p>"
        for title, col in (("Imports", "importer"), ("Exports", "exporter")):
            mine = [r for r in deals if r[ix[col]] == tag]
            if mine:
                by = replay(mine, h, ["deal"], amount)
                body += f"<h3>{title}</h3>" + chart(stack_series(by, top_names(by), d0, d1), d0, d1, wars, stacked=True, height=170)
        sellers = Counter(r[ix["exporter"]] for r in deals if r[ix["importer"]] == tag and r[ix["change"]] == "new")
        body += "<h3>Most deals bought from</h3>" + table(["exporter", "deals"], [(nm(k), n) for k, n in sellers.most_common(8)])
        out.append(("Trade", body))

    h, intl = rows_of(rec, "intelligence", country=tag)
    if intl:
        ix = {c: i for i, c in enumerate(h)}
        isop = lambda r: r[ix["kind"]] == "operation" and r[ix["change"]] == "new"
        done = [r for r in intl if isop(r) and "finished" in r[ix["key"]]]
        began = {r[ix["key"]] for r in intl if isop(r) and "finished" not in r[ix["key"]]}
        ups = [r for r in intl if r[ix["kind"]] == "agency" and r[ix["key"]] == "upgrade" and r[ix["change"]] != "removed"]
        body = f"<p>{len(began)} different operations were started and {len(done)} finished (intelligence.tsv).</p>"
        if done:
            body += table(["date", "finished operation", "result"],
                          [(esc(r[0].split(",", 1)[1].strip()), esc(r[ix["key"]].replace("finished ", "")), esc(r[ix["value"]])) for r in done])
        if ups:
            body += "<h3>Agency upgrades</h3>" + table(["date", "upgrade"], [(esc(r[0].split(",", 1)[1].strip()), esc(r[ix["value"]])) for r in ups])
        out.append(("Intelligence", body))
    return out
