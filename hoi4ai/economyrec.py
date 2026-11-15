"""Construction queues and trade: what each country builds and buys.

EconomyLog(construction_path, trade_path, names, tags, others).step(date, lines) writes two files.

construction.tsv:
  date  change  country  line  building  state  province  amount  priority  repair  produced  cost  factories  flags  was
one row per construction line of every country:
  line       the line's id: N for a building (handle 59, CBuildingProductionLine), rN for a railway (handle 4713,
             CRailwayProductionLine)
  building   the building's key (industrial_complex, arms_factory, bunker, ...; rail_way for a railway)
  state      the state it is built in; province the province, for province buildings (bunkers, naval bases, ...); for
             a railway its first and last province (FIRST>LAST) and state the first province's
  amount     levels still to build (0 for a repair); repair the levels to repair
  priority   its place in the country's queue (0 the top)
  produced   progress on the current level, of cost; factories the civilian factories on it now
  flags      conversion (a civilian factory converted to military), allied (built in another country's state)
  country    the owner of the state (the builder, unless allied)
change:
  start      a line at the first read
  new        a line queued
  changed    amount, repair or the state changed, or the line moved in its queue (was: the old values). A line
             finishing or leaving moves the lines below it up a place; that alone is no change
  done       the line ended with its last level about finished (produced near cost)
  removed    the line ended otherwise (cancelled, or the state was lost); produced and cost are the last read

trade.tsv:
  date  change  exporter  importer  resource  amount  factories  deal  started  was
one row per trade deal (handle 4713, CResourceExchange) between any two countries: amount the resource delivered per
day, factories the civilian factories the importer pays, deal its id, started its start date. change: start, new,
changed (amount or factories; was the old "amount factories"), removed. Also the auto-import settings of a country
(the trade screen's automatic trading): rows with deal "setting", exporter the country, resource the setting
(auto_import_enabled, max_factories_for_auto_import, auto_import_prioritize_fuel, auto_import_fuel_topping) and
amount its value; change start, changed (was the old value).

Reads: the construction lines and deals at the first call, each new in-game day and at once after a construction or
trade command in the record LINES (CONSTRUCTION, TRADE): one batched `bridge.write` of the ids seen before and of
the ids past the highest (new objects get higher ids); the first read probes every id. A building's state is its
location, or the province's state for province buildings (common/buildings with province_max); owners come from
memory (hoi4ai.live.Provinces). A deal's text names only the importer; the exporter is the country whose
CCountryResources (CCountry +0x11f8; the country id at CCountry +0x8) the deal points to at +0x80. Settings: TAGS each
new day, OTHERS() spread over FULL_DAYS days, and a country at once after its auto-import command (country id +0x28).
"""
import os
import re

from hoi4ai import shared
from hoi4bridge import BridgeError, BridgeUnavailable, send

FULL_DAYS = 7
PROBE = 300                 # ids past the highest to look at, finding them all
AHEAD = 50                  # building lines: ids past the highest to look at each read
AHEAD_4713 = 100            # handle 4713 (deals, railways, generals, convoys, ...) fills faster
CONSTRUCTION = ("CAddConstructionCommand", "CRemoveConstructionCommand", "CRemoveAllConstructionCommand",
                "CConvertFactoryCommand", "CReplaceBuildingCommand", "CRemoveBuildingLevelCommand",
                "CChangeProductionLinePriorityCommand", "CSetProductionLinePriorityCommand",
                "CBuildRailwayCommand", "CChangeRailwayConstructionLeveLCommand")
TRADE = ("CCreateTradeCommand",)
AUTO = ("CSetAutoImportEnabledCommand", "CSetAutoImportBudgetCommand", "CSetAutoImportPriorityCommand")
SETTINGS = ("auto_import_enabled", "max_factories_for_auto_import", "auto_import_prioritize_fuel",
            "auto_import_fuel_topping")
C_HEADER = ("date\tchange\tcountry\tline\tbuilding\tstate\tprovince\tamount\tpriority\trepair\tproduced\tcost"
            "\tfactories\tflags\twas")
T_HEADER = "date\tchange\texporter\timporter\tresource\tamount\tfactories\tdeal\tstarted\twas"
RESOURCES = 0x11f8          # CCountry -> CCountryResources
COUNTRY_ID = 0x8            # CCountry: the country id
DEAL_EXPORTER = 0x80        # CResourceExchange -> the exporter's CCountryResources
_OBJ = re.compile(r"(?:^|\n)OK (\w+)")
_FIELD = re.compile(r'\n\t(\w+)="?([^"\s{]*)"?')
_RAIL = re.compile(r"\n\trail_way=\{\s*([\d\s]*)\}")


def _day(date):
    return date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]


def _open(path, header):
    new = not os.path.exists(path)
    f = open(path, "a", encoding="utf-8")
    if new:
        f.write(header + "\n")
    return f


def province_buildings(files=None):
    """The building keys of common/buildings with a province_max level cap (built in a province, not a state)."""
    from hoi4ai import pdx
    from hoi4ai.gamefiles import GameFiles
    files = files or GameFiles()
    out = set()
    for path in files.listdir("common/buildings", ".txt").values():
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for top, _, body in pdx.parse(f.read()):
                if top != "buildings" or not isinstance(body, list):
                    continue
                for key, _, v in body:
                    if isinstance(v, list) and any(k == "level_cap" and isinstance(x, list)
                                                   and any(a == "province_max" for a, _, _ in x) for k, _, x in v):
                        out.add(key)
    return out


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _fmt(x):
    return f"{x:g}"


def write_objects(handle, ids):
    """{id: (class, text)} of the objects of HANDLE among IDS that exist (`bridge.write`, batched)."""
    out = {}
    ids = sorted(ids)
    for k in range(0, len(ids), 500):
        text = "\n".join(send(*[f"bridge.write {handle} {i}" for i in ids[k:k + 500]]))
        parts = _OBJ.split(text)
        for cls, body in zip(parts[1::2], parts[2::2]):
            body = body.split("\nFAIL", 1)[0]
            m = re.search(r"\n\tid=\{ id=(\d+) type=%d \}" % handle, body)
            if m:
                out[int(m.group(1))] = (cls, body)
    return out


class _Ids:
    """The live ids of one handle: those seen before plus the ones past the highest."""

    def __init__(self, handle, ahead):
        self.handle, self.ahead = handle, ahead
        self.ids, self.top = set(), 0

    def read(self, keep):
        """{id: (class, text)} of every object; KEEP(class) says which ids to look at again next time."""
        if not self.top:
            found, lo = {}, 1
            while True:     # PROBE at a time until a batch finds none
                batch = write_objects(self.handle, range(lo, lo + PROBE))
                found.update(batch)
                if not batch:
                    break
                self.top = max([self.top] + list(batch))
                lo += PROBE
        else:
            found = write_objects(self.handle, self.ids | set(range(self.top + 1, self.top + 1 + self.ahead)))
            while found and max(found) > self.top:
                self.top = max(found)
                found.update(write_objects(self.handle, range(self.top + 1, self.top + 1 + self.ahead)))
        self.ids = {i for i, (cls, _) in found.items() if keep(cls)}
        return found


def _tags(line, names, classes, offsets):
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] not in classes:
        return ()
    b = bytes.fromhex(p[7])
    return tuple(names.tag(int.from_bytes(b[o:o + 4], "little", signed=True)) for o in offsets if len(b) >= o + 4)


class EconomyLog:
    def __init__(self, construction_path, trade_path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.provincial = province_buildings()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.provinces, self.proc = None, None
        self.state_owner = {}
        self.lines = _Ids(59, AHEAD)
        self.objects = _Ids(4713, AHEAD_4713)
        self.held = None            # line key -> row fields
        self.deals = None           # deal id -> (exporter, importer, resource, amount, factories, started)
        self.exporters = {}         # CCountryResources address -> tag
        self.settings = {}          # tag -> {setting: value}
        self.day, self.days, self.last_read = None, 0, 0
        self.cf = _open(construction_path, C_HEADER)
        self.tf = _open(trade_path, T_HEADER)

    # --- owners ---------------------------------------------------------------------------------------------------
    def _owners(self):
        from hoi4ai.live import Provinces
        if self.provinces is None:
            self.provinces = Provinces()
            self.proc = self.provinces.proc
        pv = self.provinces.refresh()
        self.state_owner = {}
        for p in range(1, len(pv.state)):
            s = int(pv.state[p])
            if s and s not in self.state_owner:
                self.state_owner[s] = int(pv.owner[p])

    def _state_of(self, province):
        st = self.provinces.state if self.provinces is not None else ()
        return int(st[province]) if 0 < province < len(st) else 0

    def _exporter(self, deal):
        """The exporting country of deal DEAL, from memory."""
        try:
            r = send(f"bridge.object 4713 {deal}")
            m = re.search(r"@0x([0-9a-f]+)", r[0]) if r and r[0].startswith("OK") else None
            if not m or self.proc is None:
                return "?"
            res = self.proc.u64(int(m.group(1), 16) + DEAL_EXPORTER)
            if res not in self.exporters:
                for c in shared.objects("CCountry"):
                    if self.proc.class_of(c) == "CCountry":
                        r2 = self.proc.u64(c + RESOURCES)
                        if r2:
                            self.exporters[r2] = self.names.tag(self.proc.i32(c + COUNTRY_ID))
            return self.exporters.get(res, "?")
        except (BridgeError, BridgeUnavailable, OSError):
            return "?"

    # --- construction ---------------------------------------------------------------------------------------------
    def _building(self, d):
        b = d.get("template", "?")
        loc = int(d.get("location", 0) or 0)
        if b in self.provincial:
            return b, self._state_of(loc), str(loc)
        return b, loc, ""

    def _row(self, key, d, country, building, state, province):
        flags = ",".join(f for f, k in (("conversion", "conversion"), ("allied", "allied_build"))
                         if d.get(k) == "yes")
        return {"country": country, "line": key, "building": building, "state": str(state),
                "province": province, "amount": d.get("amount", "0"),
                "priority": d.get("priority", "0"), "repair": d.get("to_repair", ""),
                "produced": _fmt(_num(d.get("produced"))), "cost": _fmt(_num(d.get("cost"))),
                "factories": d.get("active_factories", "0"), "flags": flags,
                "_speed": _num(d.get("speed"))}

    def _construction(self, found59, found4713):
        lines = {}
        for lid, (cls, body) in found59.items():
            d = dict(_FIELD.findall(body))
            d.update(re.findall(r"\n\t\t(template|location)=(\S+)", body))
            lines[str(lid)] = (d,) + self._building(d)
        for oid, (cls, body) in found4713.items():
            if cls != "CRailwayProductionLine":
                continue
            d = dict(_FIELD.findall(body))
            m = _RAIL.search(body)
            path = m.group(1).split() if m else []
            first = int(path[0]) if path else 0
            lines[f"r{oid}"] = (d, "rail_way", self._state_of(first), f"{path[0]}>{path[-1]}" if path else "")
        now = {}
        for key, (d, building, state, province) in lines.items():
            if self.held and key in self.held:
                country = self.held[key]["country"]
            else:
                country = self.names.tag(self.state_owner[state]) if state in self.state_owner else "?"
            now[key] = self._row(key, d, country, building, state, province)
        rows = []
        if self.held is None:
            rows += [("start", r, "") for _, r in sorted(now.items())]
        else:
            moved = self._moved(now)
            for key, r in sorted(now.items()):
                last = self.held.get(key)
                if last is None:
                    rows.append(("new", r, ""))
                    continue
                was = [f"{k}={last[k]}" for k in ("amount", "repair", "state") if last[k] != r[k]]
                if key in moved:
                    was.append(f"priority={last['priority']}")
                if was:
                    rows.append(("changed", r, " ".join(was)))
            gap = self.days - self.last_read + 1
            for key, last in sorted(self.held.items()):
                if key not in now:
                    left = _num(last["cost"]) - _num(last["produced"])
                    done = _num(last["amount"]) <= 1 and last["_speed"] > 0 and left <= last["_speed"] * gap
                    rows.append(("done" if done else "removed", last, ""))
        self.held = now
        return rows

    def _moved(self, now):
        """The lines kept from the last read whose order in their country's queue changed."""
        out = set()
        for country in {r["country"] for r in now.values()}:
            def order(lines):
                kept = [k for k, r in lines.items() if r["country"] == country and k in now and k in self.held]
                return sorted(kept, key=lambda k: (int(lines[k]["priority"]), k))
            a, b = order(self.held), order(now)
            if a != b:
                pos = {k: i for i, k in enumerate(a)}
                out |= {k for i, k in enumerate(b) if pos[k] != i}
        return out

    # --- trade ----------------------------------------------------------------------------------------------------
    def _trade(self, found4713):
        now = {}
        for oid, (cls, body) in found4713.items():
            if cls != "CResourceExchange":
                continue
            d = dict(_FIELD.findall(body))
            if "resource" not in d:
                continue        # a convoy client, not a deal
            exp = self.deals[oid][0] if self.deals and oid in self.deals else self._exporter(oid)
            now[oid] = (exp, d.get("receiver", "?"), d["resource"], d.get("delivered", ""),
                        d.get("required_cic", ""), d.get("start_date", ""))
        rows = []
        if self.deals is None:
            rows += [("start", str(k), v, "") for k, v in sorted(now.items())]
        else:
            rows += [("new", str(k), v, "") for k, v in sorted(now.items()) if k not in self.deals]
            rows += [("changed", str(k), v, f"{self.deals[k][3]} {self.deals[k][4]}") for k, v in sorted(now.items())
                     if k in self.deals and self.deals[k][3:5] != v[3:5]]
            rows += [("removed", str(k), self.deals[k], "") for k in sorted(self.deals) if k not in now]
        self.deals = now
        return rows

    def _settings(self, tags):
        rows = []
        for tag in dict.fromkeys(t for t in tags if t and not t.isdigit() and t != "?"):
            try:
                r = shared.country(tag)
            except (BridgeError, BridgeUnavailable):
                continue
            if not r or not r[0].startswith("OK"):
                continue
            text = "\n".join(r)
            now = {}
            for k in SETTINGS:
                m = re.search(r"\n\t*%s=(\S+)" % k, text)
                if m:
                    now[k] = m.group(1)
            last = self.settings.get(tag)
            for k, v in now.items():
                if last is None:
                    rows.append(("start", "setting", (tag, "", k, v, "", ""), ""))
                elif last.get(k) != v:
                    rows.append(("changed", "setting", (tag, "", k, v, "", ""), last.get(k, "")))
            self.settings[tag] = now
        return rows

    # --- step -----------------------------------------------------------------------------------------------------
    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = _day(date)
        classes = {ln.split("\t", 3)[2] for ln in lines if ln.count("\t") >= 3}
        cmd = bool(classes & set(CONSTRUCTION + TRADE + AUTO))
        newday = day != self.day
        todo = [t for ln in lines for t in _tags(ln, self.names, AUTO + TRADE, (0x28,))]
        if newday:
            self.days += 1
            todo += self.tags
            todo += (list(self.others() or ()) if self.held is None
                     else shared.part(self.others(), self.days, FULL_DAYS))
        self.day = day
        crows, trows = [], []
        if newday or cmd:
            found59 = self.lines.read(lambda cls: True)
            found4713 = self.objects.read(lambda cls: cls in ("CResourceExchange", "CRailwayProductionLine"))
            if newday or self.held is None or any(str(i) not in self.held for i in found59):
                self._owners()
            crows = self._construction(found59, found4713)
            trows = self._trade(found4713)
            self.last_read = self.days
        trows += self._settings(todo)
        cols = C_HEADER.split("\t")[2:-1]
        for change, r, was in crows:
            self.cf.write("\t".join([date, change] + [r[c] for c in cols] + [was]) + "\n")
        for change, deal, (exp, imp, res, amount, cic, started), was in trows:
            self.tf.write("\t".join((date, change, exp, imp, res, amount, cic, deal, started, was)) + "\n")
        self.cf.flush()
        self.tf.flush()
        return len(crows) + len(trows)

    def close(self):
        self.cf.close()
        self.tf.close()
