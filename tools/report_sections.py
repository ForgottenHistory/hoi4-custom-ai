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


def done_vs(value, nm):
    """'FRA 1 SOV 2' (target, times) as 'France ×1, Soviet Union ×2'."""
    w = value.split()
    if len(w) % 2 or not all(w[i + 1].isdigit() for i in range(0, len(w), 2)):
        return nm(value)
    return ", ".join(f"{nm(w[i])} ×{w[i + 1]}" for i in range(0, len(w), 2))


def sections(rec, tag, d0, d1, wars):
    from report import LOC, chart, day, esc, nm, pretty, table
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

    # casualties: war_stats.tsv (tools/war_stats.py backfills it into hoi4-custom-ai/recordings/NAME)
    own = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordings", os.path.basename(os.path.normpath(rec)))
    h = ws = None
    for folder in (rec, own):
        h, ws = rows_of(folder, "war_stats")
        if ws:
            rec_ws = folder
            break
    if ws:
        ix = {c: i for i, c in enumerate(h)}
        last = {}
        for r in ws:
            last[(r[ix["first"]], r[ix["second"]], r[ix["start"]])] = r
        by, tot, rows = {}, {}, []
        for r in ws:
            k = (r[ix["first"]], r[ix["second"]], r[ix["start"]])
            mine_first = r[ix["first"]] == tag
            tot[k] = (int(float(r[ix["first_casualties" if mine_first else "second_casualties"]])),
                      int(float(r[ix["second_casualties" if mine_first else "first_casualties"]])))
            d = day(r[0])
            if d:
                by[(d.year, d.month)] = Counter({"our losses": sum(v[0] for v in tot.values()),
                                                 "enemy losses": sum(v[1] for v in tot.values())})
        for k, r in sorted(last.items(), key=lambda kv: kv[0][2]):
            me = "first" if k[0] == tag else "second"
            you = "second" if me == "first" else "first"
            ours, theirs = tot[k]
            rows.append((nm(k[1] if me == "first" else k[0]), esc("-".join(f"{int(x):02d}" if i else x for i, x in enumerate(r[ix["start"]].split(".")[:3]))), f"{ours:,}", f"{theirs:,}",
                         f"{theirs / ours:.1f}" if ours else "-", int(float(r[ix[me + "_captured"]])),
                         int(float(r[ix[me + "_sunk_ship"]]))))
        out.append(("Casualties",
                    f"<p>Manpower lost by {esc(tag)} and by each enemy, cumulative over every war {esc(tag)} fought (the game's own "
                    "war_relation counters, read daily from the country block: war_stats.tsv). Wars between other countries are not in it.</p>" +
                    chart(stack_series(by, ["our losses", "enemy losses"], d0, d1), d0, d1, wars, height=190) +
                    table(["enemy", "start", "our losses", "enemy losses", "kill ratio", "provinces taken", "ships sunk"], rows)))

    # battles: battles.tsv + battle_divisions.tsv (live recordings from 2026-11-17 on)
    hb, bt = rows_of(rec, "battles")
    if bt:
        ix = {c: i for i, c in enumerate(hb)}
        days, front = Counter(), Counter()
        for r in bt:
            a, b = r[ix["a_tags"]].split(), r[ix["b_tags"]].split()
            if tag in a or tag in b:
                other = {t for t in (b if tag in a else a) if t != tag and t != "?"}
                for t in other:
                    days[t] += 1
                front[r[ix["province"]]] += 1
        out.append(("Battles",
                    "<p>Land combats involving " + esc(tag) + " per enemy, counted in recorded days (a battle fought for three days counts three), "
                    "from battles.tsv (every combat in the world, daily). Damage per division is in battle_divisions.tsv "
                    "(the fall in strength between days).</p>" +
                    table(["enemy", "battle days"], [(nm(t), n) for t, n in days.most_common(12)]) +
                    "<h3>Busiest provinces</h3>" + table(["province", "battle days"], list(front.most_common(10)))))

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
                          [(esc(LOC(r["name"])), esc(pretty(r["role"])), r["attack"], r["defense"], r["planning"], r["logistics"],
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
                          [(esc(r[0].split(",", 1)[1].strip()), nm(r[ix["key"]].replace("finished ", "").strip()), done_vs(r[ix["value"]], nm)) for r in done])
        if ups:
            body += "<h3>Agency upgrades</h3>" + table(["date", "upgrade"], [(esc(r[0].split(",", 1)[1].strip()), nm(r[ix["value"]])) for r in ups])
        out.append(("Intelligence", body))
    return out
