"""In-game unit icons for the report: battalion and support-company icons as small inline PNGs.

    I = Icons(loc.roots)          # game folder, then mods in load order
    I.unit("infantry")  -> {"uri": "data:image/png;base64,...", "group": "infantry"}  or None

The icon is GFX_unit_<unit key>_icon_medium in interface/*.gfx (the unit's `sprite` in common/units/*.txt is the fallback: support companies
say `sprite = infantry` but have icons of their own),
which names a two-frame .dds strip (the first frame is the icon). Later roots (mods) override earlier ones.
Needs Pillow; without it, or for an icon it cannot find, the unit is None and the caller falls back to text.
"""
import base64
import io
import os
import re

try:
    from PIL import Image
except ImportError:
    Image = None


def blocks(text):
    """{name: body} of the top-level `name = { ... }` entries inside the sub_units block of a units file."""
    out = {}
    m = re.search(r"sub_units\s*=\s*\{", text)
    if not m:
        return out
    i, depth, start, name = m.end(), 1, None, None
    while i < len(text) and depth:
        c = text[i]
        if c == "{":
            if depth == 1:
                name_m = re.search(r"([\w\-]+)\s*=\s*$", text[:i])
                name, start = name_m.group(1) if name_m else None, i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 1 and name:
                out[name] = text[start:i]
        elif c == "#":
            i = text.find("\n", i)
            if i < 0:
                break
        i += 1
    return out


class Icons:
    def __init__(self, roots):
        self.roots = roots
        self.units = {}      # unit key -> (sprite, group)
        self.sprites = {}    # GFX name -> texture path (relative to a root)
        for r in roots:
            self._read_units(r)
            self._read_gfx(r)
        self.cache = {}

    def _read_units(self, root):
        d = os.path.join(root, "common", "units")
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not n.endswith(".txt"):
                continue
            try:
                text = open(os.path.join(d, n), encoding="utf-8-sig", errors="replace").read()
            except OSError:
                continue
            for k, body in blocks(text).items():
                sp, gr = re.search(r"\bsprite\s*=\s*([\w\-]+)", body), re.search(r"\bgroup\s*=\s*([\w\-]+)", body)
                self.units[k] = (sp.group(1) if sp else k, gr.group(1) if gr else "")

    def _read_gfx(self, root):
        d = os.path.join(root, "interface")
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if n.endswith(".gfx"):
                text = open(os.path.join(d, n), encoding="utf-8-sig", errors="replace").read()
                for m in re.finditer(r'name\s*=\s*"(GFX_unit_[\w\-]+_icon_medium)"\s+texture[fF]ile\s*=\s*"([^"]+)"', text):
                    self.sprites[m.group(1)] = m.group(2)

    def _file(self, rel):
        for r in reversed(self.roots):
            p = os.path.join(r, rel)
            if os.path.exists(p):
                return p
        return None

    def unit(self, key):
        if key in self.cache:
            return self.cache[key]
        res = None
        sprite, group = self.units.get(key, (key, ""))
        tex = self.sprites.get(f"GFX_unit_{key}_icon_medium") or self.sprites.get(f"GFX_unit_{sprite}_icon_medium")
        p = self._file(tex) if tex else None
        if p and Image:
            try:
                im = Image.open(p).convert("RGBA")
                im = im.crop((0, 0, im.width // 2, im.height))      # first of the two frames
                box = im.getbbox()
                if box:
                    im = im.crop(box)
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                res = {"uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "group": group,
                       "w": im.width, "h": im.height}
            except Exception:
                res = None
        if res is None:
            res = {"uri": None, "group": group}
        self.cache[key] = res
        return res
