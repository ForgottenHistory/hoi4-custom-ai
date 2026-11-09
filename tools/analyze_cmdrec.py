"""Compare how countries play, from a bridge.cmdrec record (see cmdrec.py).

    python analyze_cmdrec.py RECORD [--countries ITA GER ...] [--top 5]

Prints, per country (the player first, then the given countries or the busiest AIs):
commands per in-game day by class, and what each did with its orders (fronts / battle plans).
Also lists the classes whose owner couldn't be found, so owner rules can be added for them.
"""
import argparse
import os
import re
import struct
from collections import Counter, defaultdict

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"


def day_of(date_text: str) -> str:
    return re.sub(r"^\d+:\d+,?\s*", "", date_text.strip())   # "12:00, 1 January, 1936" -> "1 January, 1936"


def load(path):
    rows, player, date = [], None, "?"
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "#mark":
                text = p[2]
                if text.startswith("player="):
                    player = text[7:]
                elif text.startswith("date="):
                    date = day_of(text[5:])
                continue
            if p[0].startswith("#") or len(p) < 8:
                continue
            handles = [] if p[6] == "-" else [h.split("=")[1] for h in p[6].split(",")]
            rows.append({"date": date, "cls": p[2], "owner": p[3], "via": p[4], "mode": int(p[5]),
                         "handles": handles, "raw": bytes.fromhex(p[7]), "extra": parse_extra(p[8] if len(p) > 8 else "-")})
    return rows, player


def parse_extra(text):
    """ "D+0x40=CProvince.0xa4=6325 L+0x28=[51:3,51:9]" -> {"D+0x40": ("CProvince", 6325), "L+0x28": ["51:3", "51:9"]}"""
    out = {}
    if text == "-":
        return out
    for tok in text.split(" "):
        key, _, val = tok.partition("=")
        if key.startswith("L"):
            out[key] = [e for e in val.strip("[]").split(",") if e]
        elif key.startswith("D"):
            cls, _, num = val.partition("=")
            out[key] = (cls.split(".")[0], int(num))
        else:
            out[key] = val
    return out


def int_at(raw, off):
    return struct.unpack_from("<i", raw, off)[0]


def load_geography(game):
    """province id -> state name, from history/states/<id>-<name>.txt; and province -> (x, z) from unitstacks."""
    state, pos = {}, {}
    sdir = os.path.join(game, "history", "states")
    for fn in os.listdir(sdir):
        name = os.path.splitext(fn)[0].split("-", 1)[-1].strip()
        text = open(os.path.join(sdir, fn), encoding="utf-8-sig", errors="replace").read()
        m = re.search(r"provinces\s*=\s*\{([^}]*)\}", text)
        if m:
            for pid in m.group(1).split():
                state[int(pid)] = name
    with open(os.path.join(game, "map", "unitstacks.txt"), encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split(";")
            if len(p) > 4 and p[1] == "0":
                pos[int(p[0])] = (float(p[2]), float(p[4]))
    return state, pos


def short(cls):
    return cls[1:-7] if cls.startswith("C") and cls.endswith("Command") else cls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("record")
    ap.add_argument("--countries", nargs="*", default=[])
    ap.add_argument("--top", type=int, default=4, help="busiest AI countries to show if --countries isn't given")
    ap.add_argument("--classes", type=int, default=15, help="classes to show per country")
    ap.add_argument("--game", default=GAME, help="HOI4 folder (province and state names)")
    a = ap.parse_args()

    rows, player = load(a.record)
    days = list(dict.fromkeys(r["date"] for r in rows if r["date"] != "?"))   # in order of play
    ndays = max(len(days), 1)
    print(f"{len(rows)} commands over {len(days)} in-game days ({days[0] if days else '?'} .. {days[-1] if days else '?'}),"
          f" player {player}")

    # Coverage: classes with no owner.
    by_cls = Counter(r["cls"] for r in rows)
    unowned = Counter(r["cls"] for r in rows if r["owner"] == "-")
    print("\nOwner not found (class: unowned / total):")
    for cls, n in unowned.most_common(20):
        print(f"  {short(cls):40} {n:7} / {by_cls[cls]}")

    # The DLL takes the first handle-shaped pair that resolves, which for some classes is chance (bytes past the
    # command's end). Trust a class's owners only if most of its commands have one, found at one place.
    vias = defaultdict(Counter)
    for r in rows:
        if r["owner"] != "-":
            vias[r["cls"]][r["via"]] += 1
    trusted = {c for c, v in vias.items()
               if sum(v.values()) >= 0.8 * by_cls[c] and v.most_common(1)[0][1] >= 0.9 * sum(v.values())}
    dropped = Counter(r["cls"] for r in rows if r["owner"] != "-" and r["cls"] not in trusted)
    for r in rows:
        if r["cls"] not in trusted:
            r["owner"] = "-"
    print(f"\nOwner trusted for {len(trusted)} classes; ignored for {len(dropped)} (chance matches): " +
          ", ".join(short(c) for c, _ in dropped.most_common(8)) + (" ..." if len(dropped) > 8 else ""))

    per = defaultdict(Counter)
    for r in rows:
        if r["owner"] != "-":
            per[r["owner"]][r["cls"]] += 1
    countries = [player] if player in per else []
    if a.countries:
        countries += [c for c in a.countries if c != player]
    else:
        busiest = sorted((c for c in per if c != player), key=lambda c: -sum(per[c].values()))
        countries += busiest[:a.top]

    print("\nCommands per in-game day:")
    for c in countries:
        total = sum(per[c].values())
        print(f"\n  {c}{' (player)' if c == player else ''}: {total} commands, {total / ndays:.1f}/day")
        for cls, n in per[c].most_common(a.classes):
            print(f"    {short(cls):40} {n:7}  {n / ndays:7.2f}/day")

    # Orders (handle type 53: fronts and battle plans): how often each is touched, by what.
    print("\nOrders (fronts / battle plans):")
    for c in countries:
        touches = defaultdict(Counter)
        for r in rows:
            if r["owner"] != c:
                continue
            for h in r["handles"]:
                if h.startswith("53:"):
                    touches[h][short(r["cls"])] += 1
        if not touches:
            print(f"  {c}: none")
            continue
        kinds = Counter()
        for t in touches.values():
            kinds.update(t)
        print(f"  {c}: {len(touches)} orders touched; per order: " +
              ", ".join(f"{k} {v / len(touches):.1f}" for k, v in kinds.most_common(6)))

    movement(rows, countries, player, a.game)


def movement(rows, countries, player, game):
    """Where divisions are sent: direct moves, rail moves, and assignments to orders."""
    state, _ = load_geography(game)
    has_targets = any("L+0x40" in r["extra"] for r in rows if r["cls"] == "CMoveCommand")
    print("\nWhere divisions go" + ("" if has_targets else " (move targets not in this record: older DLL)") + ":")
    for c in countries:
        mine = [r for r in rows if r["owner"] == c]
        print(f"\n  {c}{' (player)' if c == player else ''}:")

        moves = [r for r in mine if r["cls"] == "CMoveCommand"]
        if moves:
            # province path at +0x40; its last province is where the division is sent
            for r in moves:
                path = r["extra"].get("L+0x40")
                r["dest"] = int(path[-1]) if path else None
            targets = [r["dest"] for r in moves if r["dest"]]
            units = {int_at(r["raw"], 0x3c) for r in moves}
            line = f"    moves: {len(moves)} by {len(units)} divisions"
            if targets:
                # how many divisions were sent to the same province on the same day (stacking for an attack)
                stacks = Counter((r["date"], r["dest"]) for r in moves if r["dest"])
                line += (f", {len(set(targets))} target provinces, divisions per target per day: "
                         f"mean {sum(stacks.values()) / len(stacks):.1f}, max {max(stacks.values())}")
                line += "\n      top target states: " + ", ".join(
                    f"{s} {n}" for s, n in Counter(state.get(t, f"prov {t}") for t in targets).most_common(6))
            print(line)

        rail = [r for r in mine if r["cls"] == "CStrategicRedeploymentCommand"]
        if rail:
            dest = Counter(state.get(int_at(r["raw"], 0x40), "?") for r in rail)
            print(f"    rail moves: {len(rail)} by {len({int_at(r['raw'], 0x3c) for r in rail})} units; to: " +
                  ", ".join(f"{s} {n}" for s, n in dest.most_common(6)))

        for cls, word in (("COrderAssignCommand", "assigns"), ("COrderUnassignCommand", "unassigns")):
            cmds = [r for r in mine if r["cls"] == cls and "L+0x28" in r["extra"]]
            if not cmds:
                continue
            sizes = [len(r["extra"]["L+0x28"]) for r in cmds]
            per_div = Counter(d for r in cmds for d in r["extra"]["L+0x28"])
            print(f"    {word}: {len(cmds)} commands, divisions each: mean {sum(sizes) / len(sizes):.1f} max {max(sizes)};"
                  f" {len(per_div)} divisions, {word} per division: mean {sum(per_div.values()) / len(per_div):.1f}"
                  f" max {max(per_div.values())}")

        # Churn: divisions that went back and forth between orders.
        events = defaultdict(list)
        for r in mine:
            if r["cls"] in ("COrderAssignCommand", "COrderUnassignCommand") and "L+0x28" in r["extra"]:
                order = next((h for h in r["handles"] if h.startswith("53:")), "?")
                for d in r["extra"]["L+0x28"]:
                    events[d].append(order)
        if events:
            moved = [len(set(v)) for v in events.values()]
            print(f"    orders per division: mean {sum(moved) / len(moved):.1f}, "
                  f"{sum(1 for m in moved if m >= 3)} of {len(moved)} divisions were put under 3+ different orders")


if __name__ == "__main__":
    main()
