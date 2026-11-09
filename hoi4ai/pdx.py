"""A small parser for the game's script files (key = value, key = { ... }). Comments dropped.

parse(text) -> list of (key, op, value) where value is a string or a nested list of the same; bare values in a
block come as (value, None, None).
"""
import re

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]|[<>!]?=|[<>]|[^\s{}=<>"]+')


def tokens(text):
    text = re.sub(r"#[^\n]*", "", text)
    for t in _TOKEN.findall(text):
        yield t[1:-1] if t.startswith('"') else t


def parse(text):
    toks = list(tokens(text))
    pos = 0

    def block():
        nonlocal pos
        out = []
        while pos < len(toks):
            t = toks[pos]
            pos += 1
            if t == "}":
                return out
            if t == "{":
                out.append((block(), None, None))
                continue
            if pos < len(toks) and toks[pos] in ("=", "<", ">", "<=", ">=", "!="):
                op = toks[pos]
                pos += 1
                if pos < len(toks) and toks[pos] == "{":
                    pos += 1
                    out.append((t, op, block()))
                elif pos < len(toks):
                    out.append((t, op, toks[pos]))
                    pos += 1
            else:
                out.append((t, None, None))
        return out

    return block()


def get(block, key, default=None):
    for k, _, v in block:
        if k == key:
            return v
    return default


def values(block):
    """Bare values of a block ({ a b c } -> [a, b, c])."""
    return [k for k, op, _ in block if op is None and isinstance(k, str)]
