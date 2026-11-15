"""Record a war while you (or the game's AI) fight it, for tools/compare_runs.py.

    python tools/battlerec.py --label player [--tag GER] [--target POL] [--hours 4] [--war-days 183] [--no-cmdrec]

Start the game (play the country, or `observe` to let the game's AI fight), then run this. Every --hours in-game
hours it writes every division of the country and its enemies and who controls each province
(hoi4ai/battlerec.py), its battle plans when they changed (plans.tsv,
hoi4ai/planrec.py), the country's state each day (country/, country.tsv, hoi4ai/countryrec.py), every command the game executes (bridge.cmdrec, with the date stamped in) and the event choices among them
(events.tsv, hoi4ai/eventrec.py), and every country's division templates as they are created, edited and removed
(division_templates.tsv, hoi4ai/templaterec.py), every country's equipment designs and the ones made obsolete
(equipment_designs.tsv, hoi4ai/equipmentrec.py; obsolete needs the record), and every country's production lines as
they change (production_lines.tsv, hoi4ai/productionrec.py), MIO sizes, points and traits (mios.tsv,
hoi4ai/miorec.py), market contracts and listings (market.tsv, hoi4ai/marketrec.py), every country's doctrines
(doctrines.tsv, hoi4ai/doctrinerec.py), officer corps spirits (spirits.tsv, hoi4ai/spiritrec.py),
focus, laws, advisors and national spirits (politics.tsv, hoi4ai/politicsrec.py),
construction lines, trade deals and auto-import settings (construction.tsv, trade.tsv, hoi4ai/economyrec.py),
pacts, wars, wargoals and factions (diplomacy.tsv, hoi4ai/diplomacyrec.py),
occupation laws and garrison templates (occupation.tsv, hoi4ai/occupationrec.py),
fuel prioritization, army motorization and exercises (logistics.tsv, hoi4ai/logisticsrec.py), training and deployment
(deployment.tsv, hoi4ai/deploymentrec.py), generals (generals.tsv, hoi4ai/generalrec.py), air wings (air.tsv, hoi4ai/airrec.py), and task forces and
admirals (naval.tsv, admirals.tsv, hoi4ai/navalrec.py), and research (research.tsv, hoi4ai/researchrec.py). It stops a step after the target capitulates, --war-days after the declaration, or with Ctrl+C. Our own controller records the same way by itself.

Recordings go to recordings/battle_<label>_<TAG>_<time>/.
"""
import argparse
import datetime
import os
import time

import _bridge  # noqa: F401
from hoi4bridge import BridgeError, BridgeUnavailable, send, trigger
from hoi4ai import airrec, countryrec, deploymentrec, doctrinerec, diplomacyrec, economyrec, equipmentrec, eventrec, generalrec, logisticsrec, marketrec, miorec, navalrec, occupationrec, planrec, politicsrec, productionrec, researchrec, shared, spiritrec, templaterec
from hoi4ai.battlerec import BattleRecorder
from hoi4ai.divisions import Snapshotter
from hoi4ai.live import Provinces
from controller import REPO, country_ids, enemies_of, game_time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="who fights: player, ai, ...")
    ap.add_argument("--tag", default="GER")
    ap.add_argument("--target", default="POL")
    ap.add_argument("--hours", type=int, default=4, help="in-game hours between snapshots")
    ap.add_argument("--no-cmdrec", action="store_true", help="don't record commands")
    ap.add_argument("--war-days", type=float, default=0,
                    help="stop this many in-game days after the war with TARGET began (0: when it capitulates)")
    a = ap.parse_args()
    tag, target = a.tag.upper(), a.target.upper()
    tags, ids = country_ids()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    folder = os.path.join(REPO, "recordings", f"battle_{a.label}_{tag}_{stamp}")
    rec = BattleRecorder(folder, a.label, tag, target, tags)
    if not a.no_cmdrec:
        planrec.install_rules()
        eventrec.install_rules()
        marketrec.install_rules()
        airrec.install_rules()
        print(send("bridge.cmdrec on " + os.path.join(folder, "command_record.tsv"))[0])
    events = None if a.no_cmdrec else eventrec.EventLog(os.path.join(folder, "command_record.tsv"),
                                                        os.path.join(folder, "events.tsv"))
    templates = templaterec.TemplateLog(os.path.join(folder, "division_templates.tsv"), events and events.names)
    designs = equipmentrec.DesignLog(os.path.join(folder, "equipment_designs.tsv"), events and events.names)
    production = productionrec.ProductionLog(os.path.join(folder, "production_lines.tsv"), events and events.names)
    mios = miorec.MioLog(os.path.join(folder, "mios.tsv"), [tag])
    market = marketrec.MarketLog(os.path.join(folder, "market.tsv"), events and events.names, [tag],
                                 lambda: mios.others)
    doctrines = doctrinerec.DoctrineLog(os.path.join(folder, "doctrines.tsv"), events and events.names)
    spirits = spiritrec.SpiritLog(os.path.join(folder, "spirits.tsv"), events and events.names, [tag],
                                  lambda: mios.others)
    politics = politicsrec.PoliticsLog(os.path.join(folder, "politics.tsv"), events and events.names, [tag],
                                       lambda: mios.others)
    economy = economyrec.EconomyLog(os.path.join(folder, "construction.tsv"), os.path.join(folder, "trade.tsv"),
                                    events and events.names, [tag], lambda: mios.others)
    diplomacy = diplomacyrec.DiplomacyLog(os.path.join(folder, "diplomacy.tsv"), events and events.names, [tag],
                                          lambda: mios.others)
    occupation = occupationrec.OccupationLog(os.path.join(folder, "occupation.tsv"), events and events.names, [tag],
                                             lambda: mios.others)
    logistics = logisticsrec.LogisticsLog(os.path.join(folder, "logistics.tsv"), events and events.names, [tag],
                                          lambda: mios.others)
    deployment = deploymentrec.DeploymentLog(os.path.join(folder, "deployment.tsv"), events and events.names, [tag],
                                             lambda: mios.others)
    generals = generalrec.GeneralLog(os.path.join(folder, "generals.tsv"), events and events.names, [tag],
                                     lambda: mios.others)
    air = airrec.AirLog(os.path.join(folder, "air.tsv"), events and events.names, [tag], lambda: mios.others)
    naval = navalrec.NavalLog(os.path.join(folder, "naval.tsv"), os.path.join(folder, "admirals.tsv"),
                              events and events.names, [tag], lambda: mios.others)
    research = researchrec.ResearchLog(os.path.join(folder, "research.tsv"), events and events.names, [tag],
                                       lambda: mios.others)
    plans = planrec.PlanLog(os.path.join(folder, "plans.tsv"), tag)
    country = countryrec.CountryLog(folder, tag)
    provinces = Provinces()
    snap = Snapshotter(os.path.join(REPO, ".cache", "battlerec_snap.tsv"))
    print(f"recording {tag} against {target} every {a.hours} in-game hours into {folder} (Ctrl+C stops)")
    last, done, war_start = None, False, None
    try:
        while not done:
            try:
                now = game_time(tag)
            except (BridgeError, BridgeUnavailable, ValueError) as e:
                print("date:", e)
                time.sleep(1)
                continue
            if last is not None and now < last + datetime.timedelta(hours=a.hours):
                time.sleep(0.2)
                continue
            last = now
            try:
                provinces.refresh()
                divs = snap.take()
                enemies = enemies_of(tag)
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("snapshot:", e)
                last = None
                time.sleep(1)
                continue
            at_war = target in enemies
            capitulated = at_war and trigger(f"{target} = {{ has_capitulated = yes }}")
            rec.step(now, divs, provinces.controller, enemies, at_war, capitulated)
            try:
                plans.step(f"{now:%Y-%m-%d %H:00}")
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("plans:", e)
            try:
                country.step(f"{now:%Y-%m-%d}")
            except (OSError, BridgeError, BridgeUnavailable, RuntimeError) as e:
                print("country:", e)
            if not a.no_cmdrec:
                send(f"bridge.cmdrec mark date={now:%Y-%m-%d %H:00}")
                try:
                    events.step()
                except (OSError, BridgeError, BridgeUnavailable) as e:
                    print("events:", e)
            shared.new_step(f"{now:%Y-%m-%d}")
            try:   # without the record: new template ids each day and a full read every 7 days
                templates.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("templates:", e)
            try:   # without the record: new designs each day
                designs.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("designs:", e)
            try:   # without the record: read each day
                production.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("production:", e)
            try:
                mios.step(f"{now:%Y-%m-%d %H:00}")
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("mios:", e)
            try:   # without the record: read each day
                market.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("market:", e)
            try:   # without the record: read each day
                doctrines.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("doctrines:", e)
            try:   # without the record: read each day
                spirits.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("spirits:", e)
            try:   # without the record: read each day
                politics.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("politics:", e)
            try:   # without the record: read each day
                economy.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("economy:", e)
            try:   # without the record: read each day
                diplomacy.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("diplomacy:", e)
            try:   # without the record: read each day
                occupation.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("occupation:", e)
            try:   # without the record: read each day
                logistics.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("logistics:", e)
            try:   # without the record: read each day
                deployment.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("deployment:", e)
            try:   # without the record: read each day
                generals.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("generals:", e)
            try:   # without the record: read each day
                air.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("air:", e)
            try:   # without the record: read each day
                naval.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("naval:", e)
            try:   # without the record: read each day
                research.step(f"{now:%Y-%m-%d %H:00}", events.lines if events else ())
            except (OSError, BridgeError, BridgeUnavailable) as e:
                print("research:", e)
            taken = int(((provinces.owner == ids[target]) & (provinces.controller == ids[tag])).sum())
            print(f"{now:%Y-%m-%d %H:00} war={int(at_war)} divisions={len(divs)} taken={taken}")
            done = capitulated
            if at_war and war_start is None:
                war_start = now
            if a.war_days and war_start and (now - war_start).total_seconds() / 86400 >= a.war_days:
                print(f"stopping: {a.war_days:g} days after the war began ({war_start:%Y-%m-%d %H:00})")
                done = True
    except KeyboardInterrupt:
        print()
    finally:
        if not a.no_cmdrec:
            print(send("bridge.cmdrec off")[0])
            events.step()
        if last:
            shared.new_step()
            templates.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            designs.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            production.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            market.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            doctrines.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            spirits.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            politics.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            economy.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            diplomacy.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            occupation.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            logistics.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            deployment.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            generals.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            air.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            naval.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
            research.step(f"{last:%Y-%m-%d %H:00}", events.lines if events else ())
    print("recorded into", folder)


if __name__ == "__main__":
    main()
