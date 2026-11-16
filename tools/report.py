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
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loc import Loc

sys.modules.setdefault("report", sys.modules[__name__])      # report_sections imports this module: share one LOC when run as a script

LOC = Loc()                       # replaced by build() with the game and mods of the recording
AIR_KINDS = {"small_plane": "Fighters", "small_plane_cas": "Close air support", "small_plane_naval_bomber": "Naval bombers",
             "medium_plane": "Medium bombers", "large_plane": "Heavy bombers", "transport_plane": "Transport planes"}


def nm(key, *alts):
    """Escaped display name of an id; the id itself when the game and mods have no name for it."""
    return esc(LOC(key, *alts))


def equip_name(k):
    return AIR_KINDS.get(k) or LOC(k + "_equipment", k)


MONTHS = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September October November December".split())}


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


def fold(series, keep=8):
    """At most KEEP series: the smallest are summed into one 'Other' (a ninth colour is never invented)."""
    if len(series) <= keep:
        return series
    ranked = sorted(series, key=lambda s: (-max((v for _, v in s[1]), default=0), s[0]))
    head, tail = ranked[:keep - 1], ranked[keep - 1:]
    other = [(d, sum(s[1][i][1] for s in tail if i < len(s[1]))) for i, (d, _) in enumerate(tail[0][1])]
    return head + [("Other: " + ", ".join(s[0] for s in tail), other)]


def chart(series, d0, d1, wars=(), stacked=False, height=210, ymax=None, bars=False, pct=False, colors=None):
    """SVG line/area/bar chart with a hover readout. SERIES [(name, [(date, value)])]; dates are datetime.date.
    Colours are the .s1-.s8 classes of the page CSS (fixed order, light and dark); the data of each series is also
    written into data-chart so the page script can show the values under the pointer."""
    series = fold(series)
    pick = lambda i: (colors[i] if colors and i < len(colors) else i % 8 + 1)
    W, H, L, R, T, B = 760, height, 44, 12, 12, 26
    n = max(len(s[1]) for s in series)
    top = ymax or nice(max((sum(s[1][i][1] for s in series if i < len(s[1])) for i in range(n)) if stacked
                           else (v for s in series for _, v in s[1]), default=1))
    x = Axis(d0.toordinal(), d1.toordinal(), L, W - R)
    y = Axis(0, top, H - B, T)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for k in range(6):
        v = top * k / 5
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                 f'<text x="{L - 6}" y="{y(v) + 3.5:.1f}" class="tick" text-anchor="end">{fmt(v)}{"%" if pct else ""}</text>')
    for yr in range(d0.year, d1.year + 1):
        xx = x(datetime.date(yr, 1, 1).toordinal())
        if L <= xx <= W - R:
            o.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{H - B}" class="grid year"/>'
                     f'<text x="{xx + 4:.1f}" y="{H - 8}" class="tick">{yr}</text>')
    merged = []                     # overlapping wars drawn once
    for a, b, e in sorted((day(w["start"]), day(w.get("end")) or d1, w["enemy"]) for w in wars if day(w["start"])):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2].append(e)
        else:
            merged.append([a, b, [e]])
    for a, b, es in merged:
        x0, x1 = x(max(a, d0).toordinal()), x(min(b, d1).toordinal())
        o.append(f'<rect x="{x0:.1f}" y="{T}" width="{max(1.5, x1 - x0):.1f}" height="{H - B - T}" class="war">'
                 f'<title>at war from {a} to {b}: {esc(", ".join(es[:8]))}{"..." if len(es) > 8 else ""}</title></rect>')
    base = [0.0] * n
    for i, (name, pts) in enumerate(series):
        c = f"s{pick(i)}"
        if bars:
            bw = max(1.5, (W - L - R) / max(len(pts), 1) * 0.78)
            for j, (d, v) in enumerate(pts):
                lo = base[j] if stacked else 0
                h = max(0.0, y(lo) - y(lo + v))
                if h > 0:
                    o.append(f'<rect x="{x(d.toordinal()) - bw / 2:.1f}" y="{y(lo + v):.1f}" width="{bw:.1f}" height="{h:.1f}" rx="{min(2, bw / 2):.1f}" class="f {c} gap"/>')
                if stacked:
                    base[j] += v
        elif stacked:
            poly = [(x(d.toordinal()), y(base[j] + v)) for j, (d, v) in enumerate(pts)] +                    [(x(d.toordinal()), y(base[j])) for j, (d, _) in reversed(list(enumerate(pts)))]
            o.append(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in poly)}" class="f {c} gap"/>')
            for j, (d, v) in enumerate(pts):
                base[j] += v
        else:
            o.append(f'<polyline points="{" ".join(f"{x(d.toordinal()):.1f},{y(v):.1f}" for d, v in pts)}" class="ln {c}"/>')
            if pts:
                o.append(f'<circle cx="{x(pts[-1][0].toordinal()):.1f}" cy="{y(pts[-1][1]):.1f}" r="3.2" class="f {c} dot"/>')
    o.append(f'<line class="cross" x1="0" x2="0" y1="{T}" y2="{H - B}"/></svg>')
    data = json.dumps({"x0": x.lo, "x1": x.hi, "L": L, "R": W - R, "W": W, "stacked": stacked, "pct": pct, "c": [pick(i) for i in range(len(series))],
                       "s": [[nm, [[d.toordinal(), round(v, 3)] for d, v in pts]] for nm, pts in series]}, separators=(",", ":"))
    leg = "".join(f'<span><i class="f s{pick(i)}"></i>{esc(nm)}</span>' for i, (nm, _) in enumerate(series)) if len(series) > 1 else ""
    cap = f'<figcaption class="legend">{leg}</figcaption>' if leg else ""
    return f'<figure class="fig" data-chart="{html.escape(data)}">{"".join(o)}<div class="tip" hidden></div>{cap}</figure>'


def table(head, rows, cls=""):
    """HTML table; a column whose cells are all numbers is right-aligned."""
    num = re.compile(r"^[\s\d,.\-+%kM/:→]*$")
    isnum = [all(num.match(re.sub(r"<[^>]+>", "", str(r[c]))) and str(r[c]).strip() for r in rows) if rows else False for c in range(len(head))]
    h = "".join(f'<th{" class=num" if isnum[c] else ""}>{esc(t)}</th>' for c, t in enumerate(head))
    b = "".join("<tr>" + "".join(f'<td{" class=num" if isnum[c] else ""}>{v}</td>' for c, v in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tablewrap"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def more(label, body):
    """A long list the reader opens on demand."""
    return f'<details><summary>{esc(label)}</summary>{body}</details>'


def stat(label, value, note=""):
    return f'<div class="stat"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(note)}</small></div>'


def wars_chart(wars, d0, d1):
    """One bar per war, oldest first: blue = the player declared, orange = the player was attacked."""
    rows = sorted(wars, key=lambda w: w["start"])
    W, L, R, T, RH = 760, 44, 12, 8, 15
    H = T + RH * len(rows) + 26
    x = Axis(d0.toordinal(), d1.toordinal(), L, W - R)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for yr in range(d0.year, d1.year + 1):
        xx = x(datetime.date(yr, 1, 1).toordinal())
        if L <= xx <= W - R:
            o.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T}" y2="{H - 26}" class="grid year"/><text x="{xx + 4:.1f}" y="{H - 9}" class="tick">{yr}</text>')
    for k, w in enumerate(rows):
        a, b = day(w["start"]), day(w.get("end")) or d1
        yy = T + k * RH
        o.append(f'<text x="{L - 6}" y="{yy + 10:.1f}" class="tick" text-anchor="end">{nm(w["enemy"])}</text>'
                 f'<rect x="{x(a.toordinal()):.1f}" y="{yy + 2}" width="{max(2.0, x(b.toordinal()) - x(a.toordinal())):.1f}" height="{RH - 5}" rx="2" '
                 f'class="f {"s1" if w.get("attacker") else "s2"}"><title>{nm(w["enemy"])}: {a} to {w.get("end") and b or "ongoing"} ({(b - a).days} days)</title></rect>')
    o.append("</svg>")
    return (f'<figure class="fig">{"".join(o)}<figcaption class="legend"><span><i class="f s1"></i>declared by the player</span>'
            f'<span><i class="f s2"></i>declared on the player</span></figcaption></figure>')


def load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def month_dates(keys):
    return [datetime.date(int(k[:4]), int(k[5:7]), 15) for k in keys]


EXTRA_CSS = {}                   # icon classes defined once, whatever the number of tiles


def unit_tile(key, icons):
    u = icons.unit(key)
    label = esc(LOC(key))
    cls = "u-" + re.sub(r"[^\w]", "_", key)
    if u["uri"]:
        EXTRA_CSS[cls] = f'.{cls}{{background-image:url({u["uri"]})}}'
        return f'<i class="ut {cls}" title="{label}" role="img" aria-label="{label}"></i>'
    return f'<i class="ut" title="{label}"><span>{label[:6]}</span></i>'


def templates_html(templates, d1):
    """One card per template, drawn like the in-game designer: support companies on top, then the battalion rows;
    the brigade-type (combat support) unit of a row sits at the side of it. Each `|` group of the recording is a row."""
    from icons import Icons
    icons = Icons(LOC.roots)
    seen, cards = set(), []
    for t in templates:
        if t["change"] != "new":
            continue
        rows = [r.split(",") for r in (t["battalions"] or "").split("|") if r]
        sup = [x for x in (t["support"] or "").split(",") if x]
        body = ""
        if sup:
            body += '<div class="trow sup">' + "".join(unit_tile(k, icons) for k in sup) + "</div>"
        for r in rows:
            main = [k for k in r if icons.unit(k)["group"] != "combat_support"]
            side = [k for k in r if icons.unit(k)["group"] == "combat_support"]
            body += ('<div class="trow">' + "".join(unit_tile(k, icons) for k in main) +
                     (f'<b class="side">{"".join(unit_tile(k, icons) for k in side)}</b>' if side else "") + "</div>")
        n = sum(len(r) for r in rows)
        cards.append(f'<div class="tpl"><h4>{esc(t["name"])}</h4><small>{esc(t["date"][:10])} · {n} battalions'
                     f'{f", {len(sup)} support" if sup else ""}</small>{body}</div>')
    return f'<p class="lead">{len(cards)} templates you made. Support companies on top, battalions in rows below, brigade support at the side of its row. Hover an icon for its name.</p><div class="tpls">{"".join(cards)}</div>'


def build(rec):
    global LOC
    LOC = Loc.for_recording(rec)
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
    stats = [stat("days played", f"{(d1 - d0).days:,}"),
             stat("factories", f'{first["civ"] + first["mil"] + first["naval"]:g} → {last["civ"] + last["mil"] + last["naval"]:g}'),
             stat("divisions", f'{first["divisions"]:g} → {last["divisions"]:g}'),
             stat("states controlled", f'{first["states"]:g} → {last["states"]:g}'),
             stat("wars", len(wars), f"{sum(1 for w in wars if w.get('attacker'))} declared by {meta['player']}"),
             stat("focuses", len(ps["choices"]["focus"])), stat("techs", len(ps["choices"]["research"])),
             stat("event choices", len(ps["choices"]["events"]))]
    if apm:
        stats.append(stat("APM", f'{apm["apm"]:.1f}', f'median {apm["apm_median_active"]:.0f}, p90 {apm["apm_p90_active"]:.0f}'))
    sec.append(("Summary", '<div class="stats">' + "".join(stats) + "</div>"))

    sec.append(("Economy", f'<p class="lead">Factories grew from {first["civ"] + first["mil"] + first["naval"]:g} to {last["civ"] + last["mil"] + last["naval"]:g}, divisions from {first["divisions"]:g} to {last["divisions"]:g}. Shaded bands are wars (hover for who). Source: country.tsv, one row per read.</p>'
                "<h3>Factories</h3>" + chart([("civilian", ser("civ")), ("military", ser("mil")), ("naval", ser("naval"))], d0, d1, wars, stacked=True, colors=[2, 6, 1]) +
                "<h3>Divisions and controlled states</h3>" + chart([("divisions", ser("divisions")), ("states", ser("states"))], d0, d1, wars) +
                "<h3>Manpower</h3>" + chart([("manpower", ser("manpower"))], d0, d1, wars, height=160) +
                "<h3>Stability and war support (%)</h3>" + chart([("stability", ser("stability", 100)), ("war support", ser("war_support", 100))], d0, d1, wars, height=160, ymax=100, pct=True)))

    # wars
    rows = []
    for w in sorted(wars, key=lambda w: w["start"]):
        a, b = day(w["start"]), day(w.get("end"))
        rows.append((nm(w["enemy"]), esc(a), esc(b or "ongoing"), f"{((b or d1) - a).days}", "declared" if w.get("attacker") else "attacked"))
    sec.append(("Wars", f'<p class="lead">{len(wars)} wars, {sum(1 for w in wars if w.get("attacker"))} declared by {esc(meta["player"])}. Start is the game\'s own date of the war, not the time it was read.</p>' +
                wars_chart(wars, d0, d1) + more("All wars as a table", table(["enemy", "start", "end", "days", "side"], rows))))

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
                chart([(equip_name(k), [(d, prod[m].get(k, 0)) for m, d in zip(ms, month_dates(ms))]) for k in pk], d0, d1, wars, stacked=True)))

    # choices
    ch = ps["choices"]
    foc = ch["focus"]
    sec.append(("National focus", more(f"All {len(foc)} focuses", table(["picked", "focus", "finished"], [(esc(f["date"][:10]), nm(f["focus"]), esc((f.get("finished") or "")[:10])) for f in foc]))))
    laws = [l for l in ch["laws"] if l["kind"] != "advisor"]
    adv = [l for l in ch["laws"] if l["kind"] == "advisor"]
    sec.append(("Laws and advisors", table(["date", "kind", "slot", "value", "was"], [(esc(l["date"][:10]), esc(l["kind"]), esc(l["slot"].replace("_", " ")), nm(l["value"]), nm(l["was"]) if l.get("was") else "") for l in laws + adv])))
    yr = Counter(r["date"][:4] for r in ch["research"])
    ev = Counter(r["date"][:4] for r in ch["events"])
    dc = Counter(r["date"][:4] for r in ch["decisions"])
    years = sorted(set(yr) | set(ev) | set(dc))
    sec.append(("Research, events and decisions per year",
                table(["year", "techs started", "event choices", "decisions"], [(y, yr[y], ev[y], dc[y]) for y in years]) +
                "<h3>Techs in order</h3><p class='small'>" + ", ".join(nm(r["tech"]) for r in ch["research"]) + "</p>"))
    sec.append(("Division templates", templates_html(ch["templates"], d1)))

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
:root{
  --bg:#f2f4f8;--surface:#fcfcfd;--ink:#10131a;--ink2:#4a5262;--line:#dde1e9;--accent:#2a78d6;--war:rgba(227,73,72,.10);
  --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#6250d6;--s8:#e34948;
  --display:"Space Grotesk",system-ui,sans-serif;--body:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0e1117;--surface:#171b23;--ink:#eceef3;--ink2:#b4bac8;--line:#2a303c;--accent:#3987e5;--war:rgba(230,103,103,.15);
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#0ca30c;--s7:#9085e9;--s8:#e66767;color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0e1117;--surface:#171b23;--ink:#eceef3;--ink2:#b4bac8;--line:#2a303c;--accent:#3987e5;--war:rgba(230,103,103,.15);
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#0ca30c;--s7:#9085e9;--s8:#e66767;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--body);-webkit-font-smoothing:antialiased}
.wrap{max-width:940px;margin:0 auto;padding-inline:max(16px,env(safe-area-inset-left,0px)) max(16px,env(safe-area-inset-right,0px))}
header.top{padding-block:44px 8px}
.kicker{font:600 12px/1 var(--body);letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:0 0 14px}
h1{font:700 clamp(28px,5vw,44px)/1.08 var(--display);margin:0 0 12px;letter-spacing:-.02em;text-wrap:balance}
.sub{color:var(--ink2);max-width:62ch;margin:0}
nav.toc{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);margin-top:22px}
nav.toc .wrap{display:flex;gap:6px;overflow-x:auto;padding-block:10px;scrollbar-width:thin}
nav.toc a{flex:none;color:var(--ink2);text-decoration:none;font-size:13px;padding:5px 11px;border-radius:99px;border:1px solid transparent}
nav.toc a:hover,nav.toc a:focus-visible{color:var(--ink);border-color:var(--line);background:var(--surface);outline:none}
section{padding-block:34px 4px;scroll-margin-top:52px}
h2{font:700 24px/1.2 var(--display);letter-spacing:-.01em;margin:0 0 6px}
h3{font:600 12px/1 var(--body);letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);margin:24px 0 8px}
.lead{color:var(--ink);font-size:16px;max-width:68ch;margin:0 0 14px}
p{max-width:72ch;color:var(--ink2);margin:8px 0}p.lead{color:var(--ink)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:10px;margin-top:26px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.stat span{display:block;font-size:12px;color:var(--ink2)}
.stat b{display:block;font:700 22px/1.25 var(--display);font-variant-numeric:tabular-nums;margin-top:2px;overflow-wrap:anywhere}
.stat small{display:block;color:var(--ink2);font-size:12px}
.fig{position:relative;margin:0;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 12px 10px}
.chart{display:block;width:100%;height:auto;touch-action:pan-y;overflow:visible}
.grid{stroke:var(--line);stroke-width:1}.grid.year{stroke-dasharray:2 3}
.tick{fill:var(--ink2);font:11px var(--body);font-variant-numeric:tabular-nums}
.war{fill:var(--war)}.cross{stroke:var(--ink2);stroke-width:1;opacity:0;pointer-events:none}.fig.on .cross{opacity:.7}
.f{fill:var(--c)}.gap{stroke:var(--surface);stroke-width:1.2}.ln{fill:none;stroke:var(--c);stroke-width:2;stroke-linejoin:round}.dot{stroke:var(--surface);stroke-width:2}
.s1{--c:var(--s1)}.s2{--c:var(--s2)}.s3{--c:var(--s3)}.s4{--c:var(--s4)}.s5{--c:var(--s5)}.s6{--c:var(--s6)}.s7{--c:var(--s7)}.s8{--c:var(--s8)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:8px 4px 0;font-size:12px;color:var(--ink2)}
.legend i,.tip i{background:var(--c)}.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.tip{position:absolute;top:10px;z-index:3;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 18px rgba(0,0,0,.14);min-width:120px}
.tip b{display:block;margin-bottom:4px;font-variant-numeric:tabular-nums}.tip div{display:flex;gap:6px;align-items:center;white-space:nowrap}
.tip i{width:8px;height:8px;border-radius:2px}.tip em{margin-left:auto;padding-left:14px;font-style:normal;font-variant-numeric:tabular-nums;color:var(--ink)}
.tablewrap{overflow-x:auto;margin:8px 0;background:var(--surface);border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}th{color:var(--ink2);font-weight:600;font-size:12px;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums}
details{margin:10px 0}summary{cursor:pointer;color:var(--accent);font-size:13px;font-weight:600;padding:4px 0}
summary:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.small{font-size:12px;color:var(--ink2)}ul{padding-left:20px;color:var(--ink2);max-width:72ch}li{margin:4px 0}
/* templates */
.tpls{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.tpl{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
.tpl h4{font:600 14px/1.25 var(--display);margin:0;overflow-wrap:anywhere}.tpl small{display:block;color:var(--ink2);font-size:12px;margin:2px 0 10px}
.trow{display:flex;gap:4px;margin-top:4px;align-items:center}.trow.sup{padding-bottom:8px;margin-bottom:4px;border-bottom:1px dashed var(--line)}
.ut{width:38px;height:30px;flex:none;background:#e4e6ea;border:1px solid #b9bdc6;border-radius:3px;display:flex;align-items:center;justify-content:center;font-style:normal}
.ut{background-repeat:no-repeat;background-position:center;background-size:contain;background-origin:content-box;padding:3px}.ut span{font-size:9px;color:#333}
.side{margin-left:auto;padding-left:8px;border-left:1px solid var(--line);display:flex;gap:4px}
footer{padding-block:48px;color:var(--ink2);font-size:12px}
@media (max-width:520px){h2{font-size:21px}.stat b{font-size:19px}}
"""

JS = r"""
(function(){
var fmtv=function(v,pct){return (Math.abs(v)>=100?Math.round(v):Math.round(v*100)/100).toLocaleString('en-US')+(pct?'%':'')};
document.querySelectorAll('.fig[data-chart]').forEach(function(f){
  var D=JSON.parse(f.dataset.chart),svg=f.querySelector('svg'),cross=f.querySelector('.cross'),tip=f.querySelector('.tip');
  function hide(){f.classList.remove('on');tip.hidden=true}
  function move(e){
    var r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*D.W;
    if(px<D.L||px>D.R){hide();return}
    var t=D.x0+(px-D.L)/(D.R-D.L)*(D.x1-D.x0),rows=[],ord=0;
    D.s.forEach(function(s,i){var b=s[1][0];s[1].forEach(function(q){if(Math.abs(q[0]-t)<Math.abs(b[0]-t))b=q});rows.push([s[0],b[1],i]);if(!i)ord=b[0]});
    var cx=D.L+(ord-D.x0)/(D.x1-D.x0)*(D.R-D.L);
    cross.setAttribute('x1',cx);cross.setAttribute('x2',cx);f.classList.add('on');
    tip.textContent='';
    var h=document.createElement('b');h.textContent=new Date((ord-719163)*864e5).toISOString().slice(0,10);tip.appendChild(h);
    rows.forEach(function(q){var d=document.createElement('div'),i=document.createElement('i'),n=document.createElement('span'),v=document.createElement('em');
      i.className='f s'+(D.c[q[2]]);n.textContent=q[0];v.textContent=fmtv(q[1],D.pct);d.append(i,n,v);tip.appendChild(d)});
    tip.hidden=false;var fr=f.getBoundingClientRect(),x=e.clientX-fr.left+14;
    if(x+tip.offsetWidth>fr.width-6)x=e.clientX-fr.left-tip.offsetWidth-14;
    tip.style.left=Math.max(6,x)+'px';
  }
  svg.addEventListener('pointermove',move);svg.addEventListener('pointerleave',hide);
});
})();
"""


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    meta, sec = build(a.dir)
    rec_name = os.path.basename(os.path.abspath(a.dir))
    title = f"{meta['player']} after-action report"
    nav = "".join(f'<a href="#{slug(t)}">{esc(t)}</a>' for t, _ in sec[1:])
    body = "".join(f'<section id="{slug(t)}"><h2>{esc(t)}</h2>{b}</section>' for t, b in sec)
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)}</title>"
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@600;700&display=swap">'
            f"<style>{CSS}{''.join(EXTRA_CSS.values())}</style></head><body>"
            f'<header class="top"><div class="wrap"><p class="kicker">{esc(meta["start"][:10])} to {esc(meta["end"][:10])}</p>'
            f'<h1>{esc(title)}</h1><p class="sub">{esc(rec_name)}: what happened and what you did, read from the recording\'s own files. '
            f'Hover a chart for the values.</p>{sec[0][1]}</div></header>'
            f'<nav class="toc"><div class="wrap">{nav}</div></nav>'
            f'<main class="wrap">{"".join(f"""<section id="{slug(t)}"><h2>{esc(t)}</h2>{b}</section>""" for t, b in sec[1:])}</main>'
            f'<footer class="wrap">Built by tools/report.py. Every figure is read from the recording\'s files.</footer>'
            f"<script>{JS}</script></body></html>")
    out = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", rec_name + ".html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {out} ({len(page) // 1024} KB, {len(sec)} sections)")


if __name__ == "__main__":
    main()
