"""Record every command the game executes (player and AI) while you play.

    python cmdrec.py [--dir FOLDER] [--tag GER] [--skip CLASS ...] [--interval 0.25]

--dir puts command_record.tsv and unit_snapshots.tsv in FOLDER (default: the HOI4 folder). In observer mode
(console `observe`) there is no player country; --tag names the country used to read the date (default GER).

Turns on bridge.cmdrec, then keeps stamping the in-game date into the record ("#mark" lines) until
Ctrl+C, which turns recording off. Each new in-game day it also snapshots every division (bridge.snap) into
unit_snapshots.tsv: owner, province, order, organisation candidates, strength, template.
Battle plans: the record also holds the lists behind the battle-plan commands (hoi4ai/planrec.py rules), and each
in-game hour the player's (or --tag's) lines are read from memory and written to plans.tsv when they changed.
Country state (hoi4ai/countryrec.py): each in-game day the country's whole save-format block goes to
country/DAY.txt.gz, headline numbers to country.tsv, the equipment it names to equipment.tsv, and its intel over every other country (the fog of war) to intel.tsv.
Events (hoi4ai/eventrec.py): every event any country answers, with the option it picked, to events.tsv.
Division templates (hoi4ai/templaterec.py): every country's templates at the start, then each one created, edited,
decommissioned or removed, to division_templates.tsv.
Equipment designs (hoi4ai/equipmentrec.py): every country's designs at the start, then each new design (with the one
it's based on) and each made obsolete, to equipment_designs.tsv.
Production lines (hoi4ai/productionrec.py): every country's military lines at the start, then each line added,
changed (factories, design, priority, MIO assigned or removed) or removed, to production_lines.tsv.
MIOs (hoi4ai/miorec.py): size, trait points, funds and unlocked traits of the country's MIOs each day and every
other producing country's each week, to mios.tsv (picking a trait runs no command, so this is its only record).
Market (hoi4ai/marketrec.py): every purchase contract between any two countries (seller, buyer, designs, amounts,
factories, cost) and the equipment the country has up for sale, as they appear, change and end, to market.tsv.
Doctrines (hoi4ai/doctrinerec.py): every country's grand doctrines and subdoctrines at the start, then each one
picked or replaced, to doctrines.tsv.
Analyse with analyze_cmdrec.py.
"""
import argparse
import os
import re
import time

import _bridge  # noqa: F401  (puts the hoi4-bridge client on the path)
from hoi4ai import countryrec, doctrinerec, equipmentrec, eventrec, marketrec, miorec, planrec, productionrec, templaterec
from hoi4ai.gamefiles import game_dir
from hoi4bridge import BridgeError, player_tag, query, send


def game_date(tag: str) -> str:
    lines = query(f'{tag} = {{ log = "[GetDateText]" }}')
    return lines[0].strip() if lines else "?"


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
# CArmy fields (see bridge.snap): owner, province, order id, org (+0x20, +0x28, +0x78: one is current, one max),
# strength (100000 = 100%), org x strength, unknown +0x38, template name
SNAP_FIELDS = "class=CArmy c1c8 i1e0.a4 ib0.c i20 i28 i78 i30 i40 i38 s3a8.20"
SNAP_COLUMNS = "owner province order org20 org28 org78 strength orgxstr f38 template"


def day_label(date_text: str):
    """ "12:00, 14 August, 1939" -> "1939-08-14" """
    m = re.search(r"(\d+) (\w+),? (\d{4})", date_text)
    if not m or m.group(2) not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="folder for the record and snapshots (default: the HOI4 folder)")
    ap.add_argument("--tag", help="country to read the date with (default: the player's, else GER)")
    ap.add_argument("--skip", nargs="*", default=[], help="command classes not to record")
    ap.add_argument("--interval", type=float, default=0.25, help="seconds between date checks")
    ap.add_argument("--no-snap", action="store_true", help="no daily division snapshots")
    ap.add_argument("--no-plans", action="store_true", help="no battle-plan snapshots (plans.tsv)")
    ap.add_argument("--no-country", action="store_true", help="no daily country state (country/, country.tsv)")
    ap.add_argument("--no-templates", action="store_true", help="no division template log (division_templates.tsv)")
    ap.add_argument("--no-designs", action="store_true", help="no equipment design log (equipment_designs.tsv)")
    ap.add_argument("--no-production", action="store_true", help="no production line log (production_lines.tsv)")
    ap.add_argument("--no-mios", action="store_true", help="no MIO log (mios.tsv)")
    ap.add_argument("--no-market", action="store_true", help="no market log (market.tsv)")
    ap.add_argument("--no-doctrines", action="store_true", help="no doctrine log (doctrines.tsv)")
    a = ap.parse_args()

    try:
        player = player_tag() or "none"
    except BridgeError:
        player = "none"
    tag = a.tag or (player if player != "none" else "GER")
    folder = os.path.abspath(a.dir) if a.dir else game_dir()
    os.makedirs(folder, exist_ok=True)
    if a.skip:
        print(send("bridge.cmdrec skip " + " ".join(a.skip))[0])
    planrec.install_rules()
    if not eventrec.install_rules():
        print("this bridge can't record event options (no `cmdrec field`): events.tsv will have no option")
    if not a.no_market and not marketrec.install_rules():
        print("this bridge can't record what a market listing holds (no `cmdrec deref`)")
    record = os.path.join(folder, "command_record.tsv")
    print(send("bridge.cmdrec on " + record)[0])
    events = eventrec.EventLog(record, os.path.join(folder, "events.tsv"))
    templates = None if a.no_templates else templaterec.TemplateLog(os.path.join(folder, "division_templates.tsv"),
                                                                    events.names)
    designs = None if a.no_designs else equipmentrec.DesignLog(os.path.join(folder, "equipment_designs.tsv"),
                                                               events.names)
    production = None if a.no_production else productionrec.ProductionLog(
        os.path.join(folder, "production_lines.tsv"), events.names)
    mios = None if a.no_mios else miorec.MioLog(os.path.join(folder, "mios.tsv"), [tag])
    market = None if a.no_market else marketrec.MarketLog(os.path.join(folder, "market.tsv"), events.names, [tag],
                                                          lambda: mios.others if mios else ())
    doctrines = None if a.no_doctrines else doctrinerec.DoctrineLog(os.path.join(folder, "doctrines.tsv"),
                                                                    events.names)
    plans = None if a.no_plans else planrec.PlanLog(os.path.join(folder, "plans.tsv"), tag)
    country = None if a.no_country else countryrec.CountryLog(folder, tag)
    send(f"bridge.cmdrec mark player={player}")
    print(f"player: {player}, date from {tag}")
    if not a.no_snap:
        print(send("bridge.snap reset " + os.path.join(folder, "unit_snapshots.tsv"))[0],
              "(division snapshots)")
    last, last_day = None, None
    try:
        while True:
            try:
                d = game_date(tag)
            except BridgeError as e:
                print("date:", e)
                d = None
            if d and d != last:
                n = send(f"bridge.cmdrec mark date={d}")[0]
                print(f"\r{d}  commands: {n.split()[-1]}      ", end="", flush=True)
                last = d
                day = day_label(d)
                hour = re.match(r"(\d+):", d)
                if plans and day and hour:
                    try:
                        plans.step(f"{day} {int(hour.group(1)):02d}:00")
                    except (OSError, BridgeError) as e:
                        print("plans:", e)
                if country and day:
                    try:
                        country.step(day)
                    except (OSError, BridgeError, RuntimeError) as e:
                        print("country:", e)
                try:
                    events.step()   # the mark flushed the record
                except (OSError, BridgeError) as e:
                    print("events:", e)
                if templates:
                    try:
                        templates.step(d, events.lines)
                    except (OSError, BridgeError) as e:
                        print("templates:", e)
                if designs:
                    try:
                        designs.step(d, events.lines)
                    except (OSError, BridgeError) as e:
                        print("designs:", e)
                if production:
                    try:
                        production.step(d, events.lines)
                    except (OSError, BridgeError) as e:
                        print("production:", e)
                if mios:
                    try:
                        mios.step(d)
                    except (OSError, BridgeError) as e:
                        print("mios:", e)
                if market:
                    try:
                        market.step(d, events.lines)
                    except (OSError, BridgeError) as e:
                        print("market:", e)
                if doctrines:
                    try:
                        doctrines.step(d, events.lines)
                    except (OSError, BridgeError) as e:
                        print("doctrines:", e)
                if not a.no_snap and day and day != last_day:
                    send(f"bridge.snap {day} 51 {SNAP_FIELDS}")
                    last_day = day
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
    finally:
        print(send("bridge.cmdrec off")[0])
        events.step()
        if templates and last:
            templates.step(last, events.lines)
        if designs and last:
            designs.step(last, events.lines)
        if production and last:
            production.step(last, events.lines)
        if market and last:
            market.step(last, events.lines)
        if doctrines and last:
            doctrines.step(last, events.lines)


if __name__ == "__main__":
    main()
