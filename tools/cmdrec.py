"""Record every command the game executes (player and AI) while you play.

    python cmdrec.py [--dir FOLDER] [--tag GER] [--skip CLASS ...] [--interval 0.25]

--dir puts command_record.tsv and unit_snapshots.tsv in FOLDER (default: the HOI4 folder). In observer mode
(console `observe`) there is no player country; --tag names the country used to read the date (default GER).

Turns on bridge.cmdrec, then keeps stamping the in-game date into the record ("#mark" lines) until
Ctrl+C, which turns recording off. Each new in-game day it also snapshots every division (bridge.snap) into
unit_snapshots.tsv: owner, province, order, organisation candidates, strength, template.
Analyse with analyze_cmdrec.py.
"""
import argparse
import os
import re
import time

import _bridge  # noqa: F401  (puts the hoi4-bridge client on the path)
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
    print(send("bridge.cmdrec on " + os.path.join(folder, "command_record.tsv"))[0])
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
                if not a.no_snap and day and day != last_day:
                    send(f"bridge.snap {day} 51 {SNAP_FIELDS}")
                    last_day = day
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
    finally:
        print(send("bridge.cmdrec off")[0])


if __name__ == "__main__":
    main()
