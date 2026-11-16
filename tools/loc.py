"""Localisation: turn ids (focus, tech, country tag, equipment ...) into the names the game shows.

    from loc import Loc
    L = Loc.for_recording("recordings/meme_ger")      # game + the mods the recording ran with, in load order
    L("GER") -> "Germany"       L.get("nonexistent_key") -> None      L("nonexistent_key") -> "nonexistent_key"

The base game's localisation/english is read first, then each enabled mod (dlc_load.json of the recording lists
them in load order; a later mod wins a clash, and a mod with replace_path "localisation" discards what came before).
Files under a localisation/**/replace folder are read last of each source, as the game does.
Set HOI4_DIR (game folder) and HOI4_USER_DIR (Documents/Paradox Interactive/Hearts of Iron IV) if they are not in
the default places.
"""
import json
import os
import re

GAME_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
    r"C:\Program Files\Steam\steamapps\common\Hearts of Iron IV",
    r"D:\SteamLibrary\steamapps\common\Hearts of Iron IV",
]
LINE = re.compile(r'^\s*([\w.\-\']+):\d*\s+"(.*)"\s*(?:#.*)?$')


def game_dir():
    for d in [os.environ.get("HOI4_DIR")] + GAME_CANDIDATES:
        if d and os.path.isdir(os.path.join(d, "localisation")):
            return d
    return None


def user_dir():
    d = os.environ.get("HOI4_USER_DIR") or os.path.join(os.path.expanduser("~"), "Documents", "Paradox Interactive", "Hearts of Iron IV")
    return d if os.path.isdir(d) else None


def read_yml(path, into):
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                m = LINE.match(line)
                if m:
                    into[m.group(1)] = m.group(2)
    except OSError:
        pass


def read_tree(root, into):
    """Every *_l_english.yml under ROOT/localisation; the replace folder last."""
    base = os.path.join(root, "localisation")
    files, repl = [], []
    for dp, _, names in os.walk(base):
        for n in sorted(names):
            if n.endswith("l_english.yml"):
                (repl if os.sep + "replace" in dp[len(base):] else files).append(os.path.join(dp, n))
    for p in sorted(files) + sorted(repl):
        read_yml(p, into)


def mod_info(entry, user):
    """(path, replaces_localisation) of one 'mod/xxx.mod' entry, or None."""
    f = os.path.join(user, entry) if user else None
    if not f or not os.path.exists(f):
        return None
    text = open(f, encoding="utf-8", errors="replace").read()
    m = re.search(r'^\s*path\s*=\s*"([^"]+)"', text, re.M)
    if not m:                                   # archive= mods are zipped; not read
        return None
    p = m.group(1)
    if not os.path.isabs(p):
        p = os.path.join(user, p)
    rep = any(re.search(r'replace_path\s*=\s*"localisation"', ln) for ln in text.splitlines())
    return (p, rep) if os.path.isdir(p) else None


def brace_blocks(text, name):
    """Bodies of every `name = { ... }` block in TEXT (comments dropped), nested braces kept."""
    text = re.sub(r"#[^\n]*", "", text)
    out = []
    for m in re.finditer(r"\b%s\s*=\s*\{" % name, text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append(text[m.end():i - 1])
    return out


def read_scripted(root, into):
    """common/scripted_localisation: {name: [(trigger body, localization_key)]}; a later definition replaces."""
    d = os.path.join(root, "common", "scripted_localisation")
    if not os.path.isdir(d):
        return
    for n in sorted(os.listdir(d)):
        if not n.endswith(".txt"):
            continue
        text = open(os.path.join(d, n), encoding="utf-8-sig", errors="replace").read()
        for body in brace_blocks(text, "defined_text"):
            nm = re.search(r"\bname\s*=\s*(\w+)", body)
            if not nm:
                continue
            opts = []
            for t in brace_blocks(body, "text"):
                key = re.search(r"localization_key\s*=\s*([\w.\-']+)", t)
                trig = brace_blocks(t, "trigger")
                if key:
                    opts.append((trig[0] if trig else "", key.group(1)))
            into[nm.group(1)] = opts


class Loc:
    def __init__(self, tables=None):
        self.t = tables or {}
        self.sources = []          # what was read, for the report's own caveat
        self.missing = []          # enabled mods that could not be read
        self.scripted = {}         # scripted localisation: name -> [(trigger, key)]
        self.ctx = {}              # what its triggers are checked against: tag, government, focuses (a set)
        self.roots = []            # game folder then each mod folder, in load order (for icons)

    @classmethod
    def for_recording(cls, rec, mods=None):
        self = cls()
        game, user = game_dir(), user_dir()
        if game:
            read_tree(game, self.t)
            self.sources.append("base game")
            self.roots.append(game)
        if mods is None:
            try:
                mods = json.load(open(os.path.join(rec, "dlc_load.json"), encoding="utf-8")).get("enabled_mods", [])
            except (OSError, ValueError):
                mods = []
        for entry in mods:
            info = mod_info(entry, user)
            if not info:
                self.missing.append(entry)
                continue
            path, replaces = info
            if replaces:
                self.t.clear()
            read_tree(path, self.t)
            self.sources.append(os.path.basename(path))
            self.roots.append(path)
        for r in self.roots:
            read_scripted(r, self.scripted)
        return self

    def holds(self, trigger):
        """Whether a scripted-localisation trigger holds for self.ctx. Only has_government, tag, original_tag and
        has_completed_focus can be told from a recording; anything else counts as false. No trigger: true."""
        c = self.ctx
        conds = re.findall(r"(\w+)\s*=\s*([\w.\-]+)", trigger)
        if not conds:
            return not trigger.strip()
        ok = {"has_government": lambda v: v == c.get("government"),
              "tag": lambda v: v == c.get("tag"), "original_tag": lambda v: v == c.get("tag"),
              "has_completed_focus": lambda v: v in c.get("focuses", ())}
        return all(ok.get(k, lambda v: False)(v) for k, v in conds)

    def scripted_text(self, name):
        """The text of [name] for self.ctx: the first branch that holds, else the first branch, else None."""
        opts = self.scripted.get(name)
        if not opts:
            return None
        for trig, key in opts:
            if self.holds(trig):
                return self.get(key, 0)
        return self.get(opts[0][1], 0)

    def clean(self, text):
        for _ in range(3):                                   # $KEY$ references first (they may hold [GetXxx]), then scripted names
            text = re.sub(r"\$([\w.\-']+)(?:\|[^$]*)?\$", lambda m: self.get(m.group(1), 0) or "", text)
            text = re.sub(r"\[(\w+)\]", lambda m: self.scripted_text(m.group(1)) if m.group(1) in self.scripted else m.group(0), text)
        text = re.sub(r"\[([A-Z]{3})\.Get(?:Name|NameDef|Adjective)\]", lambda m: self.get(m.group(1)) or m.group(1), text)
        text = re.sub(r"\[[^\]]*\]", "", text)
        text = re.sub(r"§.|£\w+|\n", lambda m: " " if m.group(0) == "\n" else "", text)
        return re.sub(r"\s+", " ", text).strip()

    def get(self, key, depth=1):
        v = self.t.get(key)
        if v is None:
            return None
        return self.clean(v) if depth else v

    def __call__(self, key, *alts):
        """Name of KEY (or of the first of ALTS that has one); the id itself when none has."""
        for k in (key,) + alts:
            v = self.get(k)
            if v:
                return v
        return str(key)
