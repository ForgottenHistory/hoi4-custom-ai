"""Who owns, controls and has a core on every state, and who controls every province, as the running game has it now.

    python tools/state_snapshot.py RECORDING_DIR     (writes into hoi4-custom-ai/recordings/<name>)

For a recording made before states.tsv and control.tsv existed (hoi4ai/countryrec.py): writes REC/states_now.tsv
(date, state, owner, controller, cores) and REC/control_now.tsv (date, province, controller), dated by the game's
clock. Also REC/subjects_now.tsv (date, subject, overlord, autonomy state id, overlord_colour yes/no: the map paints such a
subject in its overlord's colour) and REC/names_now.tsv (date, tag, the game's own name for the country). It is one day, not a history: the game must be at (or near) the
recording's end.
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


def autonomy_ids(files):
    """{autonomy state id: uses the overlord's colour?} from every `autonomy_state = { id = ... }` block of
    common/autonomous_states/*.txt (base game and mods, load order)."""
    ids = {}
    for path in files.listdir("common/autonomous_states", ".txt").values():
        text = re.sub(r"#[^\n]*", "", open(path, encoding="utf-8-sig", errors="replace").read())
        for chunk in re.split(r"\bautonomy_state\s*=\s*\{", text)[1:]:
            m = re.search(r"\bid\s*=\s*(\w+)", chunk)
            if m:
                ids[m.group(1)] = bool(re.search(r"\buse_overlord_color\s*=\s*yes", chunk))
    return ids


def subjects(files):
    """[(subject, overlord, autonomy state id, uses overlord colour yes/no)] for every subject now (one script query)."""
    ids = autonomy_ids(files)
    body = " ".join(f'if = {{ limit = {{ has_autonomy_state = {a} }} overlord = {{ log = "S|[PREV.GetTag]|[THIS.GetTag]|{a}" }} }}'
                    for a in ids)
    out = []
    for line in query("every_country = { limit = { exists = yes is_subject = yes } " + body + " }"):
        p = line.strip().split("|")
        if len(p) == 4 and p[0] == "S":
            out.append((*p[1:], "yes" if ids.get(p[3]) else "no"))
    return sorted(set(out))


def country_names():
    """{tag: the game's own name for it now} (script [GetName]: ideology, scripted and dynamic names resolved)."""
    out = {}
    for line in query('every_country = { limit = { exists = yes } log = "N|[THIS.GetTag]|[THIS.GetName]" }'):
        p = line.strip().split("|", 2)
        if len(p) == 3 and p[0] == "N":
            out[p[1]] = p[2]
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    from hoi4ai.gamefiles import GameFiles
    load = os.path.join(sys.argv[1], "dlc_load.json")
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
    subs = subjects(GameFiles(load=load if os.path.exists(load) else None))
    with open(os.path.join(rec, "subjects_now.tsv"), "w", encoding="utf-8") as f:
        f.write("date\tsubject\toverlord\tautonomy\toverlord_colour\n")
        for s in subs:
            f.write(f"{day}\t" + "\t".join(s) + "\n")
    nm = country_names()
    with open(os.path.join(rec, "names_now.tsv"), "w", encoding="utf-8") as f:
        f.write("date\ttag\tname\n")
        for t, v in sorted(nm.items()):
            f.write(f"{day}\t{t}\t{v}\n")
    print(f"{day}: {len(now)} states -> states_now.tsv, {n} provinces -> control_now.tsv, {len(subs)} subjects -> "
          f"subjects_now.tsv, {len(nm)} names -> names_now.tsv")


if __name__ == "__main__":
    main()
