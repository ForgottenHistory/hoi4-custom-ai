"""The player's government over a recording: [(date, ruling party ideology group)], only where it changed.

    from government import history
    history("recordings/meme_ger") -> [("1936-01-01", "fascism")]

Read from the ruling_party column of country.tsv when the recording has it; older recordings have it only inside
the daily country dumps (country/DAY.txt.gz, politics={ ruling_party= }), which are read once and cached in
REC/government.tsv. Run it by hand to build that cache:  python tools/government.py recordings/meme_ger
"""
import gzip
import os
import re
import sys


def _from_column(rec):
    path = os.path.join(rec, "country.tsv")
    try:
        with open(path, encoding="utf-8") as f:
            head = f.readline().rstrip("\n").split("\t")
            if "ruling_party" not in head:
                return None
            i = head.index("ruling_party")
            rows = [ln.rstrip("\n").split("\t") for ln in f]
    except OSError:
        return None
    return [(r[0], r[i]) for r in rows if len(r) > i and r[i]]


def _from_dumps(rec):
    d = os.path.join(rec, "country")
    out = []
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not n.endswith(".txt.gz"):
            continue
        with gzip.open(os.path.join(d, n), "rt", encoding="utf-8", errors="replace") as g:
            m = re.search(r"^\t\t\truling_party=(\w+)", g.read(), re.M)
        if m:
            out.append((n[:-7], m.group(1)))
    return out


def history(rec):
    cache = os.path.join(rec, "government.tsv")
    rows = _from_column(rec)
    if rows is None:
        try:
            with open(cache, encoding="utf-8") as f:
                rows = [tuple(ln.rstrip("\n").split("\t")) for ln in f.readlines()[1:]]
        except OSError:
            rows = _from_dumps(rec)
            with open(cache, "w", encoding="utf-8") as f:
                f.write("date\truling_party\n" + "".join(f"{a}\t{b}\n" for a, b in rows))
    out = []
    for d, g in rows:
        if not out or out[-1][1] != g:
            out.append((d, g))
    return out


def at(hist, date):
    """The government on DATE ("YYYY-MM-DD"): the last change on or before it, else the first."""
    cur = hist[0][1] if hist else None
    for d, g in hist:
        if d <= date:
            cur = g
    return cur


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for d, g in history(sys.argv[1]):
        print(d, g)
