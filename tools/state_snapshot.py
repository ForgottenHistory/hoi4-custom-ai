"""Who owns, controls and has a core on every state, and who controls every province, as the running game has it now.

    python tools/state_snapshot.py RECORDING_DIR     (writes into hoi4-custom-ai/recordings/<name>)

For a recording made before states.tsv and control.tsv existed (hoi4ai/countryrec.py): writes REC/states_now.tsv
(date, state, owner, controller, cores) and REC/control_now.tsv (date, province, controller), dated by the game's
clock. It is one day, not a history: the game must be at (or near) the recording's end.
"""
import os
import re
import sys

import _bridge  # noqa: F401
from hoi4bridge import query
from hoi4ai.countryrec import states
from hoi4ai.eventrec import Names
from hoi4ai.live import Provinces

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]


def game_day():
    text = query('GER = { log = "[GetDateText]" }')[0]
    m = re.search(r"(\d+) (\w+), (\d+)", text)
    return f"{int(m.group(3)):04d}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    # outputs always go inside this project: hoi4-custom-ai/recordings/<recording name>
    rec = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordings",
                       os.path.basename(os.path.normpath(sys.argv[1])))
    os.makedirs(rec, exist_ok=True)
    day = game_day()
    now = states()
    with open(os.path.join(rec, "states_now.tsv"), "w", encoding="utf-8") as f:
        f.write("date\tstate\towner\tcontroller\tcores\n")
        for sid, v in sorted(now.items()):
            f.write(f"{day}\t{sid}\t{v[0]}\t{v[1]}\t{v[2]}\n")
    ctrl, names, n = Provinces().refresh().controller, Names(), 0
    with open(os.path.join(rec, "control_now.tsv"), "w", encoding="utf-8") as f:
        f.write("date\tprovince\tcontroller\n")
        for p in range(1, len(ctrl)):
            if int(ctrl[p]):
                f.write(f"{day}\t{p}\t{names.tag(int(ctrl[p]))}\n")
                n += 1
    print(f"{day}: {len(now)} states -> states_now.tsv, {n} provinces -> control_now.tsv")


if __name__ == "__main__":
    main()
