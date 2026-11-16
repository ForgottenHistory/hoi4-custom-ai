"""After-action report of a recording: one self-contained HTML page (inline SVG, no scripts, no network).

    python report.py RECORDING_DIR [--out FILE]

Run examples.py, then playstyle.py (and apm.py, paradrops.py --landed if you want those sections) on the folder first;
this reads their files and writes hoi4-custom-ai/reports/<recording name>.html (or FILE):
  playstyle.json   economy, army, production, wars, choices (required)
  apm.json         actions per minute (optional)
  paradrops.tsv    paradrop lines and whether they landed (optional)
  reloads.json     reloads of an older save (optional; shown as a caveat)
  occupation, naval, air, generals, admirals, trade, intelligence .tsv   the sections of the same names (report_sections.py)
Every number on the page is read from those files; nothing is written by hand. Dates are the game's own where the
files give them (wars: the war's real start); a pick's date is the date of the command that made it, `logged` the
time the recorder read it.
"""
import argparse
import datetime
import html
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MONTHS = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September October November December".split())}
PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#9c755f", "#bab0ac", "#ff9da7"]


def day(text):
    """'1937-05-09 21:00', '1937-05-09' or '21:00, 29 April, 1937' -> date, or None."""
    if not text:
        return None
    t = text.strip()
    try:
        return datetime.date.fromisoformat(t[:10])
    except ValueError:
        pass
    try:
        _, dm, y = [x.strip() for x in t.split(",")]
        d, mo = dm.split()
        return datetime.date(int(y), MONTHS[mo], int(d))
    except (ValueError, KeyError):
        return None


def esc(x):
    return html.escape(str(x))


class Axis:
    def __init__(self, lo, hi, a, b):
        self.lo, self.hi, self.a, self.b = lo, hi if hi != lo else lo + 1, a, b

    def __call__(self, v):
        return self.a + (v - self.lo) / (self.hi - self.lo) * (self.b - self.a)


def nice(v):
    if v <= 0:
        return 1
    p = 10 ** (len(str(int(v))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * p:
            return m * p
    return 10 * p


def fmt(v):
    v = float(v)
    return f"{v / 1e6:.1f}M" if abs(v) >= 1e6 else f"{v / 1e3:.0f}k" if abs(v) >= 1e4 else f"{v:g}"


def chart(series, d0, d1, wars=(), stacked=False, height=210, ymax=None, bars=False, pct=False):
    """SVG line/area/bar chart. SERIES [(name, [(date, value)])]; dates are datetime.date."""
    W, H, L, R, T, B = 760, height, 46, 10, 10, 24
    top = ymax or nice(max((sum(s[1][i][1] for s in series if i < len(s[1])) if stacked else max((v for _, v in s[1]), default=0))
                           for i in range(max(len(s[1]) for s in series))) if stacked else
                       max((v for s in series for _, v in s[1]), default=1))
    x = Axis(d0.toordinal(), d1.toordinal(), L, W - R)
    y = Axis(0, top, H - B, T)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for k in range(6):
        v = top * k / 5
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                 f'<text x="{L - 5}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{fmt(v)}{"%" if pct else ""}</text>')
    for yr in range(d0.year, d1.year + 1):
        xx = x(datetime.date(yr, 1, 1).toordinal())
        if L <= xx <= W - R:
            o.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{H - B}" class="grid"/>'
                     f'<text x="{xx + 3:.1f}" y="{H - 8}" class="tick">{yr}</text>')
    spans = sorted((day(w["start"]), day(w.get("end")) or d1, w["enemy"]) for w in wars if day(w["start"]))
    merged = []                     # overlapping wars drawn once
    for a, b, e in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2].append(e)
        else:
            merged.append([a, b, [e]])
    for a, b, es in merged:
        w = {"start": str(a), "enemy": ", ".join(es[:8])}
        if True:
            o.append(f'<rect x="{x(max(a, d0).toordinal()):.1f}" y="{T}" width="{max(1.5, x(min(b, d1).toordinal()) - x(max(a, d0).toordinal())):.1f}" '
                     f'height="{H - B - T}" class="war"><title>war from {esc(w["start"])} ({esc(w["enemy"])})</title></rect>')
    base = [0.0] * max(len(s[1]) for s in series)
    for i, (name, pts) in enumerate(series):
        col = PALETTE[i % len(PALETTE)]
        if bars:
            bw = max(1.5, (W - L - R) / max(len(pts), 1) * 0.8)
            for j, (d, v) in enumerate(pts):
                lo = base[j] if stacked else 0
                o.append(f'<rect x="{x(d.toordinal()) - bw / 2:.1f}" y="{y(lo + v):.1f}" width="{bw:.1f}" height="{max(0, y(lo) - y(lo + v)):.1f}" fill="{col}">'
                         f'<title>{esc(name)} {d}: {v:g}</title></rect>')
                if stacked:
                    base[j] += v
        elif stacked:
            top_pts = [(d, base[j] + v) for j, (d, v) in enumerate(pts)]
            poly = [(x(d.toordinal()), y(v)) for d, v in top_pts] + [(x(d.toordinal()), y(base[j])) for j, (d, _) in reversed(list(enumerate(pts)))]
            o.append(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in poly)}" fill="{col}" fill-opacity=".85"><title>{esc(name)}</title></polygon>')
            for j, (d, v) in enumerate(pts):
                base[j] += v
        else:
            o.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.8" points="{" ".join(f"{x(d.toordinal()):.1f},{y(v):.1f}" for d, v in pts)}"><title>{esc(name)}</title></polyline>')
    o.append("</svg>")
    leg = "".join(f'<span><i style="background:{PALETTE[i % len(PALETTE)]}"></i>{esc(n)}</span>' for i, (n, _) in enumerate(series))
    return "".join(o) + f'<div class="legend">{leg}</div>'


def table(head, rows, cls=""):
    h = "".join(f"<th>{esc(c)}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def stat(label, value, note=""):
    return f'<div class="stat"><b>{esc(value)}</b><span>{esc(label)}</span><small>{esc(note)}</small></div>'


def load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def month_dates(keys):
    return [datetime.date(int(k[:4]), int(k[5:7]), 15) for k in keys]


def build(rec):
    ps = load(os.path.join(rec, "playstyle.json"))
    if not ps:
        raise SystemExit("playstyle.json missing: run examples.py and playstyle.py first")
    apm = load(os.path.join(rec, "apm.json"))
    reloads = load(os.path.join(rec, "reloads.json"))
    meta = ps["meta"]
    d0, d1 = day(meta["start"]), day(meta["end"])
    eco = [(day(r["date"]), r) for r in ps["economy"] if day(r["date"])]
    wars = ps["wars"]
    sec = []

    def ser(key, scale=1.0):
        return [(d, r[key] * scale) for d, r in eco if r.get(key) is not None]

    # headline
    last = eco[-1][1]
    first = eco[0][1]
    stats = [stat("span", f'{d0} to {d1}', f"{(d1 - d0).days} days"),
             stat("factories", f'{first["civ"] + first["mil"] + first["naval"]:g} → {last["civ"] + last["mil"] + last["naval"]:g}'),
             stat("divisions", f'{first["divisions"]:g} → {last["divisions"]:g}'),
             stat("states controlled", f'{first["states"]:g} → {last["states"]:g}'),
             stat("wars", len(wars), f"{sum(1 for w in wars if w.get('attacker'))} declared by {meta['player']}"),
             stat("focuses", len(ps["choices"]["focus"])), stat("techs", len(ps["choices"]["research"])),
             stat("event choices", len(ps["choices"]["events"]))]
    if apm:
        stats.append(stat("APM", f'{apm["apm"]:.1f}', f'median {apm["apm_median_active"]:.0f}, p90 {apm["apm_p90_active"]:.0f}'))
    sec.append(("Summary", '<div class="stats">' + "".join(stats) + "</div>"))

    sec.append(("Economy", "<p>Shaded bands are wars. Source: country.tsv, one row per read.</p>"
                "<h3>Factories</h3>" + chart([("civilian", ser("civ")), ("military", ser("mil")), ("naval", ser("naval"))], d0, d1, wars, stacked=True) +
                "<h3>Divisions and controlled states</h3>" + chart([("divisions", ser("divisions")), ("states", ser("states"))], d0, d1, wars) +
                "<h3>Manpower</h3>" + chart([("manpower", ser("manpower"))], d0, d1, wars, height=160) +
                "<h3>Stability and war support (%)</h3>" + chart([("stability", ser("stability", 100)), ("war support", ser("war_support", 100))], d0, d1, wars, height=160, ymax=100, pct=True)))

    # wars
    rows = []
    for w in sorted(wars, key=lambda w: w["start"]):
        a, b = day(w["start"]), day(w.get("end"))
        rows.append((esc(w["enemy"]), esc(a), esc(b or "ongoing"), f"{((b or d1) - a).days}", "declared" if w.get("attacker") else "attacked"))
    sec.append(("Wars", "<p>Start is the game's own date of the war, not the time it was read.</p>" +
                table(["enemy", "start", "end", "days", "side"], rows)))

    # army
    mon = ps["army"]["monthly"]
    ks = ps["meta"]["kinds"]
    sec.append(("Army and your commands",
                "<h3>Divisions and how many moved each day</h3>" +
                chart([("divisions", [(day(r["date"]), r["divisions"]) for r in ps["army"]["days"]]),
                       ("moved since yesterday", [(day(r["date"]), r["moved"]) for r in ps["army"]["days"]])], d0, d1, wars) +
                "<h3>Your army commands per month, by kind</h3>" +
                chart([(k, [(d, mon[m].get(k, 0)) for m, d in zip(sorted(mon), month_dates(sorted(mon)))]) for k in ks], d0, d1, wars, stacked=True, bars=True)))

    # production
    prod = ps["production"]
    pk = sorted({k for m in prod.values() for k in m}, key=lambda k: -max(m.get(k, 0) for m in prod.values()))
    ms = sorted(prod)
    sec.append(("Production", "<p>Military factories on each kind of equipment, replayed from production_lines.tsv.</p>" +
                chart([(k, [(d, prod[m].get(k, 0)) for m, d in zip(ms, month_dates(ms))]) for k in pk], d0, d1, wars, stacked=True)))

    # choices
    ch = ps["choices"]
    foc = ch["focus"]
    sec.append(("National focus", table(["picked", "focus", "finished"], [(esc(f["date"][:10]), esc(f["focus"]), esc((f.get("finished") or "")[:10])) for f in foc])))
    laws = [l for l in ch["laws"] if l["kind"] != "advisor"]
    adv = [l for l in ch["laws"] if l["kind"] == "advisor"]
    sec.append(("Laws and advisors", table(["date", "kind", "slot", "value", "was"], [(esc(l["date"][:10]), esc(l["kind"]), esc(l["slot"]), esc(l["value"]), esc(l.get("was") or "")) for l in laws + adv])))
    yr = Counter(r["date"][:4] for r in ch["research"])
    ev = Counter(r["date"][:4] for r in ch["events"])
    dc = Counter(r["date"][:4] for r in ch["decisions"])
    years = sorted(set(yr) | set(ev) | set(dc))
    sec.append(("Research, events and decisions per year",
                table(["year", "techs started", "event choices", "decisions"], [(y, yr[y], ev[y], dc[y]) for y in years]) +
                "<h3>Techs in order</h3><p class='small'>" + ", ".join(esc(r["tech"]) for r in ch["research"]) + "</p>"))
    sec.append(("Division templates", table(["date", "name", "battalions", "support"], [
        (esc(t["date"][:10]), esc(t["name"]), esc(t["battalions"] or ""), esc(t["support"] or "")) for t in ch["templates"] if t["change"] == "new"])))

    # paradrops
    pd_path = os.path.join(rec, "paradrops.tsv")
    if os.path.exists(pd_path):
        rows = [ln.rstrip("\n").split("\t") for ln in open(pd_path, encoding="utf-8")][1:]
        rows = [r + [""] * (10 - len(r)) for r in rows]
        landed = [r for r in rows if r[7] == "yes"]
        by = Counter((day(r[0]).year, r[7]) for r in rows if day(r[0]))
        states = Counter(r[6] for r in rows)
        tl = sorted({(day(r[8]).year, day(r[8]).month) for r in landed if day(r[8])})
        per = Counter((day(r[8]).year, day(r[8]).month) for r in landed if day(r[8]))
        body = (f"<p>{len(rows)} paradrop lines ordered, {len(landed)} landed. A landing is a division of the order's army moving from the "
                "airbase province to the target province between two snapshots; a drop between snapshots shows as no (see docs/tools.md).</p>" +
                table(["year", "landed", "not landed"], [(y, by[(y, "yes")], by[(y, "no")]) for y in sorted({k[0] for k in by})]) +
                "<h3>Landings per month</h3>" +
                (chart([("landed", [(datetime.date(y, m, 15), per[(y, m)]) for y, m in tl])], d0, d1, wars, bars=True, height=150) if tl else "") +
                "<h3>Most targeted states</h3>" + table(["state", "lines"], [(esc(s), n) for s, n in states.most_common(10)]))
        sec.append(("Paratroopers", body))

    # APM
    if apm:
        bc = sorted(apm["by_class"].items(), key=lambda kv: -kv[1]["actions"])[:12] if isinstance(apm.get("by_class"), dict) else []
        sec.append(("Your play (APM)", f'<p>{apm["actions"]} actions from {apm["commands"]} commands in {apm["record_minutes"]:.0f} minutes of play; '
                    f'busiest minute {apm["peak"]["actions"]} actions, at {esc(apm["peak"]["date"])}.</p>' + (table(["command class", "actions", "commands"], [(esc(k), v["actions"], v["commands"]) for k, v in bc]) if bc else "")))

    import report_sections
    sec += report_sections.sections(rec, meta["player"], d0, d1, wars)

    # caveats
    cav = ["Dates of focus, law and tech rows are the day of the command that made them (`date`); `logged` is when the recorder read them, which can be later.",
           "A reload of an older save repeats a stretch of dates in every file; use tools/rows.py --final for those files.",
           "Paradrop landings are read from daily snapshots; a drop and move inside one gap shows as not landed.",
           "Controlled-state counts may include the states of integrated puppets (not verified)."]
    if reloads:
        cav.insert(0, f"This recording has reloads: {esc(json.dumps(reloads)[:300])}. The economy and army rows above may repeat that stretch.")
    sec.append(("Reading this report", "<ul>" + "".join(f"<li>{c}</li>" for c in cav) + "</ul>"))
    return meta, sec


CSS = """
:root{--bg:#fafaf8;--fg:#1c1c1a;--mut:#6b6b66;--card:#fff;--line:#e2e2dc;--war:rgba(200,60,50,.13)}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--fg:#e8e8e2;--mut:#9a9a92;--card:#1f1f1c;--line:#34342f;--war:rgba(230,90,80,.18)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{margin:0 0 4px;font-size:28px}h2{margin:40px 0 8px;font-size:20px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:14px;margin:18px 0 4px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}.sub{color:var(--mut)}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}
.stat b{display:block;font-size:19px}.stat span{color:var(--mut);font-size:12px}.stat small{display:block;color:var(--mut);font-size:11px}
.chart{width:100%;height:auto}.grid{stroke:var(--line);stroke-width:1}.tick{fill:var(--mut);font-size:10px}.war{fill:var(--war)}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--mut)}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:600}
.small{font-size:12px;color:var(--mut)}ul{padding-left:20px}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    meta, sec = build(a.dir)
    title = f"After-action report: {meta['player']}, {meta['start'][:10]} to {meta['end'][:10]}"
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main><h1>{esc(title)}</h1>"
            f'<p class="sub">Built by tools/report.py from the recording in {esc(os.path.basename(os.path.abspath(a.dir)))}; every figure is read from its files.</p>' +
            "".join(f"<section><h2>{esc(t)}</h2>{b}</section>" for t, b in sec) + "</main></body></html>")
    name = os.path.basename(os.path.abspath(a.dir))
    out = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", name + ".html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {out} ({len(page) // 1024} KB, {len(sec)} sections)")


if __name__ == "__main__":
    main()
