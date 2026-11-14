"""Event choices from a recorded command_record.tsv, to events.tsv beside it (or OUT).

    python tools/events.py RECORD [OUT]

cmdrec.py and battlerec.py write events.tsv while recording; this decodes a record afterwards (an older one, say).
Event and option names come from the running game, so run it with the same mods loaded. Records made before the
bridge could read the option (bridge.cmdrec field) have no option. Layout: hoi4ai/eventrec.py.
"""
import os
import sys

import _bridge  # noqa: F401
from hoi4ai.eventrec import EventLog


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rec = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(rec)), "events.tsv")
    print(EventLog(rec, out).step(), "event choices to", out)


if __name__ == "__main__":
    main()
