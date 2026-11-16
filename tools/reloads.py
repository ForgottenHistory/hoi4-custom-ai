"""List the reloads of an older save in a cmdrec recording.

    python reloads.py RECORDING_DIR [--out FILE]

A reload shows in command_record.tsv as "#mark ... date=" going back in time (usually through a `date=?` mark while the
game loads). Every file of the recording then holds the stretch from the load point to the reload twice: the abandoned
timeline and the replay. Prints each reload (record line, ms, the date before and after, the hours rewound) and writes
RECORDING_DIR/reloads.json. `rows.py DIR FILE --final` drops the abandoned rows of any .tsv; the record's own clock
(hoi4ai/clock.py) follows the rewind, so command dates are the replay's.
A mark more than REWIND (72 hours) behind the latest one counts: a mark is read before it is written, so it can be late
by some hours but never that far.
"""
import argparse
import json
import os

import _bridge  # noqa: F401  (puts the repo on sys.path)

from hoi4ai.clock import HOUR, REWIND, parse


def reloads(path):
    """[{"line", "ms", "from", "to", "hours", "unreadable_marks"}] in order."""
    out, top, top_text, unreadable = [], None, "", 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if not line.startswith("#mark\t"):
                continue
            p = line.rstrip("\r\n").split("\t")
            if len(p) < 3 or not p[2].startswith("date="):
                continue
            text = p[2][5:].strip()
            d, style = parse(text)
            if d is None:
                unreadable += 1
                continue
            if top is not None and d < top - REWIND:
                out.append({"line": n, "ms": int(p[1]) if p[1].isdigit() else None, "from": top_text, "to": text,
                            "hours": int((top - d) / HOUR), "unreadable_marks": unreadable})
                top = None
                unreadable = 0
            if top is None or d > top:
                top, top_text = d, text
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    found = reloads(os.path.join(a.dir, "command_record.tsv"))
    for r in found:
        print(f"line {r['line']}: ms {r['ms']}: {r['from']} -> {r['to']} ({r['hours'] / 24:.1f} days back)")
    if not found:
        print("no reloads")
    out = a.out or os.path.join(a.dir, "reloads.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(found, f, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
