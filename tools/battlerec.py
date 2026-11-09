"""Record a war while you (or the game's AI) fight it, for tools/compare_runs.py.

    python tools/battlerec.py --label player [--tag GER] [--target POL] [--hours 4] [--no-cmdrec]

Start the game (play the country, or `observe` to let the game's AI fight), then run this. Every --hours in-game
hours it writes every division of the country and its enemies and who controls each province
(hoi4ai/battlerec.py), and every command the game executes (bridge.cmdrec, with the date stamped in). It stops a
step after the target capitulates, or with Ctrl+C. Our own controller records the same way by itself.

Recordings go to recordings/battle_<label>_<TAG>_<time>/.
"""
import argparse
import datetime
import os
import time

import _bridge  # noqa: F401
from hoi4bridge import BridgeError, BridgeUnavailable, send, trigger
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
    a = ap.parse_args()
    tag, target = a.tag.upper(), a.target.upper()
    tags, ids = country_ids()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    folder = os.path.join(REPO, "recordings", f"battle_{a.label}_{tag}_{stamp}")
    rec = BattleRecorder(folder, a.label, tag, target, tags)
    if not a.no_cmdrec:
        print(send("bridge.cmdrec on " + os.path.join(folder, "command_record.tsv"))[0])
    provinces = Provinces()
    snap = Snapshotter(os.path.join(REPO, ".cache", "battlerec_snap.tsv"))
    print(f"recording {tag} against {target} every {a.hours} in-game hours into {folder} (Ctrl+C stops)")
    last, done = None, False
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
            if not a.no_cmdrec:
                send(f"bridge.cmdrec mark date={now:%Y-%m-%d %H:00}")
            taken = int(((provinces.owner == ids[target]) & (provinces.controller == ids[tag])).sum())
            print(f"{now:%Y-%m-%d %H:00} war={int(at_war)} divisions={len(divs)} taken={taken}")
            done = capitulated
    except KeyboardInterrupt:
        print()
    finally:
        if not a.no_cmdrec:
            print(send("bridge.cmdrec off")[0])
    print("recorded into", folder)


if __name__ == "__main__":
    main()
