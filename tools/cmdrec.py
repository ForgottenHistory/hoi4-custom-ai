"""Record every command the game executes (player and AI) while you play.

    python cmdrec.py [--dir FOLDER] [--tag GER] [--skip CLASS ...] [--interval 0.25]

--dir puts command_record.tsv and unit_snapshots.tsv in FOLDER (default: the HOI4 folder). In observer mode
(console `observe`) there is no player country; --tag names the country used to read the date (default GER).

Turns on bridge.cmdrec, then keeps stamping the in-game date into the record ("#mark" lines) every --interval
seconds until Ctrl+C, which turns recording off. The logs below read on a thread at each new date the main loop
sees (a step takes ~1-2 s, longer than a day at speed 5, so the logs skip dates; rows are dated when read, and
the marks stay on time; --profile prints what each step took). Each new in-game day it also snapshots every division (bridge.snap) into
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
Officer corps spirits (hoi4ai/spiritrec.py): the army, navy and air spirits of the country each day and of every
other producing country each week, as they are picked and replaced, to spirits.tsv.
Politics (hoi4ai/politicsrec.py): the national focus (current, paused, completed), laws, advisors and national
spirits of the country each day and of every other producing country each week, and of a country at once after its
focus, law or advisor command, to politics.tsv.
Economy (hoi4ai/economyrec.py): every country's construction lines, buildings and railways (building, state,
levels, place in the queue, progress) as they are queued, change and end, to construction.tsv; every trade deal
between any two countries (exporter, importer, resource, amount, factories) as it starts, changes and ends, and the
auto-import settings, to trade.tsv.
Diplomacy (hoi4ai/diplomacyrec.py): each country's pacts (guarantees, access, puppets, wars, ...), wargoals, opinion
modifiers, diplomats sent, incoming proposals and faction, as they change, to diplomacy.tsv.
Occupation (hoi4ai/occupationrec.py): occupation laws and garrison templates, globally, per occupied country and
per state, as they change, to occupation.tsv.
Decisions (hoi4ai/decisionrec.py): the decisions taken and ignored (from the commands), and each country's running
and available decisions and missions as they change, to decisions.tsv.
Intelligence (hoi4ai/intelligencerec.py): each country's intelligence agency (creation, upgrades, name), its
operatives and their missions, the operatives it can recruit, cryptology and operations, as they change, to
intelligence.tsv.
Logistics (hoi4ai/logisticsrec.py): fuel prioritization (army, air, navy: low / medium / high) and each army's
motorization priority and exercises, of the country each day and of every other producing country each week, as it changes, to logistics.tsv.
Training and deployment (hoi4ai/deploymentrec.py): the deployment orders and the divisions in training under them,
of the country each day and of every other producing country each week, as they change and deploy, to deployment.tsv.
Generals (hoi4ai/generalrec.py): the generals and field marshals of the country each day and of every other
producing country each week (skills, traits, the army they command), as they change, to generals.tsv.
Air wings (hoi4ai/airrec.py): the air wings of the country each day and of every other producing country each week
(air base, mission, region, day or night, planes), as they change, to air.tsv.
Navies (hoi4ai/navalrec.py): the task forces and admirals of the country each day and of every other producing
country each week (ships, mission, regions, engagement, repair, home base, admiral; admirals' skills and traits), as
they change, to naval.tsv and admirals.tsv.
Research (hoi4ai/researchrec.py): the tech and design company in each research slot of the country each day and of
every other producing country each week, and the techs finished, to research.tsv.
Analyse with analyze_cmdrec.py.
"""
import argparse
import os
import re
import threading
import time

import _bridge  # noqa: F401  (puts the hoi4-bridge client on the path)
from hoi4ai import airrec, countryrec, decisionrec, deploymentrec, doctrinerec, diplomacyrec, economyrec, equipmentrec, eventrec, generalrec, intelligencerec, logisticsrec, marketrec, miorec, navalrec, occupationrec, planrec, politicsrec, productionrec, researchrec, shared, spiritrec, templaterec
from hoi4ai.gamefiles import game_dir
from hoi4bridge import BridgeError, BridgeUnavailable, player_tag, query, send


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
    ap.add_argument("--no-spirits", action="store_true", help="no officer corps spirit log (spirits.tsv)")
    ap.add_argument("--no-politics", action="store_true", help="no focus, law and advisor log (politics.tsv)")
    ap.add_argument("--no-economy", action="store_true",
                    help="no construction and trade logs (construction.tsv, trade.tsv)")
    ap.add_argument("--no-diplomacy", action="store_true", help="no diplomacy log (diplomacy.tsv)")
    ap.add_argument("--no-occupation", action="store_true", help="no occupation law and garrison log (occupation.tsv)")
    ap.add_argument("--no-decisions", action="store_true", help="no decision log (decisions.tsv)")
    ap.add_argument("--no-intelligence", action="store_true", help="no intelligence agency log (intelligence.tsv)")
    ap.add_argument("--no-logistics", action="store_true", help="no logistics settings log (logistics.tsv)")
    ap.add_argument("--no-deployment", action="store_true", help="no training and deployment log (deployment.tsv)")
    ap.add_argument("--no-generals", action="store_true", help="no generals log (generals.tsv)")
    ap.add_argument("--no-air", action="store_true", help="no air wing log (air.tsv)")
    ap.add_argument("--no-naval", action="store_true", help="no navy log (naval.tsv, admirals.tsv)")
    ap.add_argument("--profile", action="store_true", help="print how long each log's step takes")
    ap.add_argument("--no-research", action="store_true", help="no research log (research.tsv)")
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
    if not a.no_air and not airrec.install_rules():
        print("this bridge can't record the wings of an air command (no `cmdrec list`)")
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
    spirits = None if a.no_spirits else spiritrec.SpiritLog(os.path.join(folder, "spirits.tsv"), events.names, [tag],
                                                            lambda: mios.others if mios else ())
    politics = None if a.no_politics else politicsrec.PoliticsLog(
        os.path.join(folder, "politics.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    economy = None if a.no_economy else economyrec.EconomyLog(
        os.path.join(folder, "construction.tsv"), os.path.join(folder, "trade.tsv"), events.names, [tag],
        lambda: mios.others if mios else ())
    diplomacy = None if a.no_diplomacy else diplomacyrec.DiplomacyLog(
        os.path.join(folder, "diplomacy.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    occupation = None if a.no_occupation else occupationrec.OccupationLog(
        os.path.join(folder, "occupation.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    decisions = None if a.no_decisions else decisionrec.DecisionLog(
        os.path.join(folder, "decisions.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    intelligence = None if a.no_intelligence else intelligencerec.IntelligenceLog(
        os.path.join(folder, "intelligence.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    logistics = None if a.no_logistics else logisticsrec.LogisticsLog(
        os.path.join(folder, "logistics.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    deployment = None if a.no_deployment else deploymentrec.DeploymentLog(
        os.path.join(folder, "deployment.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    generals = None if a.no_generals else generalrec.GeneralLog(
        os.path.join(folder, "generals.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    air = None if a.no_air else airrec.AirLog(
        os.path.join(folder, "air.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    naval = None if a.no_naval else navalrec.NavalLog(
        os.path.join(folder, "naval.tsv"), os.path.join(folder, "admirals.tsv"), events.names, [tag],
        lambda: mios.others if mios else ())
    research = None if a.no_research else researchrec.ResearchLog(
        os.path.join(folder, "research.tsv"), events.names, [tag], lambda: mios.others if mios else ())
    plans = None if a.no_plans else planrec.PlanLog(os.path.join(folder, "plans.tsv"), tag)
    country = None if a.no_country else countryrec.CountryLog(folder, tag)
    loggers = [(n, v) for n, v in (("templates", templates), ("designs", designs), ("production", production),
                                   ("mios", mios), ("market", market), ("doctrines", doctrines),
                                   ("spirits", spirits), ("politics", politics), ("economy", economy),
                                   ("diplomacy", diplomacy), ("occupation", occupation),
                                   ("decisions", decisions), ("intelligence", intelligence),
                                   ("logistics", logistics), ("deployment", deployment),
                                   ("generals", generals), ("air", air), ("naval", naval), ("research", research))
               if v]
    send(f"bridge.cmdrec mark player={player}")
    print(f"player: {player}, date from {tag}")
    if not a.no_snap:
        print(send("bridge.snap reset " + os.path.join(folder, "unit_snapshots.tsv"))[0],
              "(division snapshots)")
    latest = {"date": None}         # the newest date, set by the main loop every INTERVAL
    wake, stop = threading.Event(), threading.Event()

    def step(d):
        day = day_label(d)
        hour = re.match(r"(\d+):", d)
        timing, t0 = [], time.perf_counter()
        if plans and day and hour:
            try:
                plans.step(f"{day} {int(hour.group(1)):02d}:00")
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("plans:", e)
        timing.append(("plans", time.perf_counter() - t0))
        t0 = time.perf_counter()
        if country and day:
            try:
                country.step(day)
            except (OSError, BridgeError, BridgeUnavailable, RuntimeError) as e:
                print("country:", e)
        timing.append(("country", time.perf_counter() - t0))
        t0 = time.perf_counter()
        try:
            events.step()   # the marks flushed the record
        except (OSError, BridgeError, BridgeUnavailable) as e:
            print("events:", e)
        timing.append(("events", time.perf_counter() - t0))
        shared.new_step(day)
        for name, log in loggers:
            d = latest["date"] or d     # the game moves on while the logs read: date the rows when read
            t0 = time.perf_counter()
            try:
                log.step(d) if name == "mios" else log.step(d, events.lines)
            except Exception as e:      # noqa: BLE001  one log failing mustn't stop the others
                print(f"\n{name}: {type(e).__name__}: {e}")
            timing.append((name, time.perf_counter() - t0))
        if a.profile and sum(t for _, t in timing) > 0.1:
            print(f"\n  {sum(t for _, t in timing):.2f}s " + " ".join(f"{n}={t:.2f}" for n, t in timing if t >= 0.05))

    def work():
        done = None
        while not stop.is_set():
            wake.wait(1)
            wake.clear()
            d = latest["date"]
            if d and d != done and not stop.is_set():
                done = d
                step(d)

    # The logs read on a thread, so the main loop keeps stamping dates into the record (each command is dated by the
    # mark before it) while a step takes longer than the game spends on a day at high speed.
    worker = threading.Thread(target=work, name="logs", daemon=True)
    worker.start()
    last, last_day = None, None
    try:
        while True:
            try:
                d = game_date(tag)
            except (BridgeError, BridgeUnavailable) as e:
                print("date:", e)
                d = None
            if d and d != last:
                n = send(f"bridge.cmdrec mark date={d}")[0]
                print(f"\r{d}  commands: {n.split()[-1]}      ", end="", flush=True)
                last = latest["date"] = d
                wake.set()
                day = day_label(d)
                if not a.no_snap and day and day != last_day:
                    send(f"bridge.snap {day} 51 {SNAP_FIELDS}")
                    last_day = day
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        wake.set()
        worker.join()
        print(send("bridge.cmdrec off")[0])
        events.step()
        shared.new_step()
        for name, log in loggers if last else ():
            if name != "mios":
                log.step(last, events.lines)


if __name__ == "__main__":
    main()
