"""Read a recording's .tsv files by COLUMN NAME, so a tag is never matched in the wrong column.

    python rows.py DIR FILE --schema
    python rows.py DIR FILE [col=value | col!=value | col~substring ...] [--cols a,b,c] [--limit N] [--width N]
    python rows.py DIR FILE [filters] --count COLUMN

FILE is the name with or without .tsv (diplomacy, research, politics, facilities, country, ...). Every filter names a
column of that file's header; an unknown column is an error that lists the real ones, so you cannot grep `GER` and
catch it in a "partner" or "leader" column by accident. --schema prints the header and the format the file's
recorder documents (the module docstring), which says what each column holds for each `kind`. Filters are ANDed.
Examples:
    python rows.py REC diplomacy kind=faction country=HUN        # HUN's faction rows (partner = the leader)
    python rows.py REC diplomacy kind=faction partner=GER        # the members of GER's faction
    python rows.py REC politics country=GER slot=focus value~GER_ --cols date,change,value
    python rows.py REC facilities country=GER --count kind

Read DOCS "Reading the recordings" in docs/tools.md before drawing conclusions: dates are the time of the READ, a
reload repeats a stretch of dates, and the focus "completed" rows arrive in batches.
"""
import argparse
import glob
import os
import re
import sys
from collections import Counter

import _bridge  # noqa: F401  (puts the repo on sys.path)

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "hoi4ai")


def load(path):
    """(header, rows): rows are lists padded to the header's width. The header is the first line."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    header = lines[0].split("\t")
    rows = []
    for ln in lines[1:]:
        if not ln or ln.startswith("#"):
            continue
        p = ln.split("\t")
        rows.append(p + [""] * (len(header) - len(p)))
    return header, rows


def module_doc(filename):
    """The docstring of the recorder module that names FILENAME, or ""."""
    for path in sorted(glob.glob(os.path.join(PKG, "*.py"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r'\s*"""(.*?)"""', text, re.S)
        if m and filename in m.group(1):
            return f"[{os.path.basename(path)}]\n{m.group(1)}"
    return ""


def parse_filter(arg, header):
    m = re.match(r"^([A-Za-z_][\w]*)(!=|~|=)(.*)$", arg)
    if not m:
        sys.exit(f"bad filter {arg!r}: use column=value, column!=value or column~substring")
    col, op, val = m.groups()
    if col not in header:
        sys.exit(f"no column {col!r} in this file; its columns are: {', '.join(header)}")
    i = header.index(col)
    return {"=": lambda r: r[i] == val, "!=": lambda r: r[i] != val, "~": lambda r: val in r[i]}[op]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("file")
    ap.add_argument("filters", nargs="*")
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--cols")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--width", type=int, default=60, help="truncate each value to this many characters (0: no limit)")
    ap.add_argument("--count", metavar="COLUMN")
    a = ap.parse_args()
    name = a.file if a.file.endswith(".tsv") else a.file + ".tsv"
    path = os.path.join(a.dir, name)
    if not os.path.exists(path):
        sys.exit(f"{path} not found; files: {', '.join(sorted(os.path.basename(p) for p in glob.glob(os.path.join(a.dir, '*.tsv'))))}")
    header, rows = load(path)
    if a.schema:
        print("columns:", " | ".join(header))
        print(module_doc(name) or "(no recorder docstring names this file)")
        return
    tests = [parse_filter(f, header) for f in a.filters]
    rows = [r for r in rows if all(t(r) for t in tests)]
    if a.count:
        if a.count not in header:
            sys.exit(f"no column {a.count!r}; columns: {', '.join(header)}")
        i = header.index(a.count)
        for v, n in Counter(r[i] for r in rows).most_common():
            print(f"{n:8d}  {v}")
        return
    cols = a.cols.split(",") if a.cols else header
    for c in cols:
        if c not in header:
            sys.exit(f"no column {c!r}; columns: {', '.join(header)}")
    idx = [header.index(c) for c in cols]
    cut = (lambda s: s[:a.width] + "…" if a.width and len(s) > a.width else s)
    print("\t".join(cols))
    for r in rows[:a.limit]:
        print("\t".join(cut(r[i]) for i in idx))
    print(f"-- {len(rows)} rows" + (f", showing {a.limit}" if len(rows) > a.limit else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
