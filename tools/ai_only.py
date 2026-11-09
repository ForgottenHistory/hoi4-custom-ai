"""Leave the game's AI running only for some countries: the console's `ai TAG` for every other country.

    python tools/ai_only.py GER SOV        # every other country stops (no research, production, diplomacy, moves)
    python tools/ai_only.py --all          # AI back on for every country

`ai TAG` toggles one country's AI and says which way it went ("Disabled AI for: TAG" / "Enabled AI for: TAG"),
so a country that ends up the wrong way is toggled again. Run it after loading a save: the console setting is
probably not saved. Countries with their AI off still exist: their divisions defend where they stand, events
still fire, and others' diplomatic requests to them may wait unanswered.
"""
import argparse
import sys

import _bridge  # noqa: F401
from hoi4bridge import query, send
from controller import _tags


def countries():
    """Every country that exists now."""
    lines = query('GER = { every_country = { log = "[THIS.GetFlag]" } }')
    return sorted(set(_tags(lines)))


def set_ai(tag, on):
    """True when TAG's AI ends up ON (or off), toggling at most twice."""
    for _ in range(2):
        r = send(f"ai {tag}")[0]
        if ("Enabled" in r) == on and ("Disabled" in r) != on:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keep", nargs="*", help="countries whose AI keeps running")
    ap.add_argument("--all", action="store_true", help="turn the AI back on for every country")
    a = ap.parse_args()
    if not a.keep and not a.all:
        ap.error("name the countries to keep, or --all")
    keep = {t.upper() for t in a.keep}
    tags = countries()
    if not tags:
        sys.exit("no countries found (is a game loaded?)")
    off, on, bad = [], [], []
    for t in tags:
        want = a.all or t in keep
        (on if want else off).append(t) if set_ai(t, want) else bad.append(t)
    print(f"AI on for {len(on)}: {' '.join(on) if len(on) < 30 else ''}")
    print(f"AI off for {len(off)} countries")
    if bad:
        print("could not set:", " ".join(bad))
    missing = keep - set(tags)
    if missing:
        print("not in the game:", " ".join(sorted(missing)))


if __name__ == "__main__":
    main()
