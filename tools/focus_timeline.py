"""Rebuild the player's national focus timeline from a recording's country/DAY.txt.gz files.

    python focus_timeline.py RECORDING_DIR [--out FILE]

For recordings made before hoi4ai/politicsrec.py read the focus block with matched braces, politics.tsv lost the focus
whenever one was highlighted in the UI and listed the completed focuses in batches. The daily country blocks hold the
truth: this reads each day's `focus={ ... }` and writes FILE (default RECORDING_DIR/focus_rebuilt.tsv) in politics.tsv's
columns (date change country kind slot value was) with kind=focus and slot=completed or current, dated by the day file
(a day, not an hour; days skipped by the recorder are folded into the next one). `rows.py` reads it like any log.
A reload overwrites the day files of the repeated days, so the replay is what is read.
"""
import argparse
import glob
import gzip
import os
import re

import _bridge  # noqa: F401  (puts the repo on sys.path)

import examples
from hoi4ai.politicsrec import focus_body

HEADER = "date\tchange\tcountry\tkind\tslot\tvalue\twas"


def day_state(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        body = focus_body(f.read())
    if body is None:
        return None
    cur = re.search(r'current="([^"]*)"', body)
    return list(dict.fromkeys(re.findall(r'completed="([^"]*)"', body))), cur.group(1) if cur else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    ap.add_argument("--tag")
    a = ap.parse_args()
    tag = a.tag or examples.player_of(os.path.join(a.dir, "command_record.tsv")) or "?"
    rows, done, current = [], set(), ""
    for path in sorted(glob.glob(os.path.join(a.dir, "country", "*.txt.gz"))):
        day = os.path.basename(path)[:10]
        st = day_state(path)
        if st is None:
            continue
        completed, cur = st
        for f in completed:
            if f not in done:
                rows.append((day, "new", tag, "focus", "completed", f, ""))
        for f in sorted(done - set(completed)):
            rows.append((day, "removed", tag, "focus", "completed", "", f))
        done = set(completed)
        if cur != current:
            rows.append((day, "removed" if not cur else ("new" if not current else "changed"), tag, "focus", "current",
                         cur, current))
            current = cur
    out = a.out or os.path.join(a.dir, "focus_rebuilt.tsv")
    with open(out, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    print(f"{tag}: {len(rows)} rows, {len(done)} focuses completed by the last day; wrote {out}")


if __name__ == "__main__":
    main()
