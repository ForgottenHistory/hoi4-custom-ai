"""Facts about a war from the game's own war summary.

casualties(a, b): the losses each side has suffered in the war between A and B, as the war summary screen counts
them. The game keeps them in the war_relation of the attacker's relation to the defender (save format:
first, second, first_casualties, second_casualties), so both countries' blocks are searched.
"""
import re

import hoi4bridge as bridge

_WAR = re.compile(r"\bwar_relation=\{")


def _blocks(text):
    """The insides of every war_relation={...} in TEXT."""
    for m in _WAR.finditer(text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            c = text[i]
            depth += (c == "{") - (c == "}")
            i += 1
        yield text[m.end():i - 1]


def _field(text, key):
    m = re.search(r"\b" + key + r'="?([^"\s}]+)', text)
    return m.group(1) if m else None


def casualties(a, b):
    """(A's losses, B's losses) in the war between them, or None when they are not at war."""
    for tag in (a, b):
        for w in _blocks(bridge.country_block(tag)):
            first, second = _field(w, "first"), _field(w, "second")
            if {first, second} != {a, b}:
                continue
            n = lambda k: int(float(_field(w, k) or 0))
            fc, sc = n("first_casualties"), n("second_casualties")
            return (fc, sc) if first == a else (sc, fc)
    return None
