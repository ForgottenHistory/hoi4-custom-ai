"""Regimental support of every division template as the running game has it now, for a recording made before
division_templates.tsv had its `regiment` column. Writes REC/regiment_now.tsv (id, owner, name, regiment) that
tools/report.py shows when the recording itself lacks the column. Needs the game running (bridge loaded).

    python tools/regiment_snapshot.py recordings/meme_ger
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from hoi4ai.templaterec import TemplateLog


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = os.path.join(sys.argv[1], "regiment_now.tsv")
    now = TemplateLog(os.devnull).scan()
    with open(out, "w", encoding="utf-8") as f:
        f.write("id\towner\tname\tregiment\n")
        for tid, v in sorted(now.items()):
            f.write(f"{tid}\t{v[0]}\t{v[1]}\t{v[5]}\n")
    print(f"wrote {out} ({len(now)} templates)")


if __name__ == "__main__":
    main()
