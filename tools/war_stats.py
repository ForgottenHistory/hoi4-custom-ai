"""War casualties and war-score parts per day, from a recording's daily country blocks (country/DAY.txt.gz).

    python tools/war_stats.py RECORDING_DIR

Writes hoi4-custom-ai/recordings/<name>/war_stats.tsv: date, then one row per war the country is in whenever a value
changed (columns: hoi4ai/countryrec.WAR_COLS). The same rows are written live by CountryLog for recordings made from
2026-11-17 on; this backfills older ones. Only the recorded country's own wars are in its block.
Read it with: python tools/rows.py recordings/NAME war_stats.tsv first=GER second=POL
"""
import glob
import gzip
import os
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hoi4-bridge", "client")]
from hoi4ai.countryrec import WAR_COLS, war_rows


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordings",
                       os.path.basename(os.path.normpath(src)))
    os.makedirs(out, exist_ok=True)
    last, n = {}, 0
    with open(os.path.join(out, "war_stats.tsv"), "w", encoding="utf-8") as f:
        f.write("date\t" + "\t".join(WAR_COLS) + "\n")
        for path in sorted(glob.glob(os.path.join(src, "country", "*.txt.gz"))):
            day = os.path.basename(path)[:10]
            with gzip.open(path, "rt", encoding="utf-8") as g:
                block = g.read()
            for row in war_rows(block):
                if last.get(tuple(row[:3])) != row:
                    last[tuple(row[:3])] = row
                    f.write(day + "\t" + "\t".join(row) + "\n")
                    n += 1
    print(f"{n} rows, {len(last)} wars -> {os.path.join(out, 'war_stats.tsv')}")


if __name__ == "__main__":
    main()
