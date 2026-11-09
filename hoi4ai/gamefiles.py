"""Find game files the way the game does: base game, then the active mods in load order.

The active mods are the ones the launcher wrote to dlc_load.json for the last launch. A later mod's file wins
over an earlier one's and over the base game's; a mod's replace_path hides the base game's and earlier mods'
files in that folder (that folder only, not its subfolders).
"""
import ctypes
import json
import os
import re
import subprocess

STEAM_DEFAULT = r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"


def _running_game_dir():
    """Folder of the running hoi4.exe, or None."""
    try:
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq hoi4.exe", "/FO", "CSV", "/NH"],
                                      creationflags=0x08000000).decode("latin-1")
        pid = int(out.split(",")[1].strip('"'))
    except (subprocess.CalledProcessError, IndexError, ValueError, OSError):
        return None
    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(len(buf))
        if not k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(n)):
            return None
        return os.path.dirname(buf.value)
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def _documents_dir():
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
    docs = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    p = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(docs), 0, None, ctypes.byref(p)) == 0:
        path = p.value
        ctypes.windll.ole32.CoTaskMemFree(p)
        return path
    return os.path.join(os.path.expanduser("~"), "Documents")


def game_dir():
    """HOI4_DIR, else the running game's folder, else Steam's default."""
    return os.environ.get("HOI4_DIR") or _running_game_dir() or STEAM_DEFAULT


def user_dir():
    """HOI4_USER_DIR, else Documents\\Paradox Interactive\\Hearts of Iron IV."""
    return os.environ.get("HOI4_USER_DIR") or os.path.join(_documents_dir(), "Paradox Interactive", "Hearts of Iron IV")


def _norm(rel):
    return rel.replace("\\", "/").strip("/").lower()


class Mod:
    def __init__(self, descriptor, root, name, replace):
        self.descriptor, self.root, self.name = descriptor, root, name
        self.replace = {_norm(r) for r in replace}

    def __repr__(self):
        return "Mod(%r, %r)" % (self.name, self.root)


def _parse_descriptor(path, udir):
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    text = re.sub(r"#[^\n]*", "", text)
    def one(key):
        m = re.search(r'(?m)^\s*%s\s*=\s*"([^"]*)"' % key, text)
        return m.group(1) if m else None
    root = one("path") or one("archive")
    if root and not os.path.isabs(root):
        root = os.path.join(udir, root)
    replace = re.findall(r'(?m)^\s*replace_path\s*=\s*"([^"]*)"', text)
    return Mod(path, root and os.path.normpath(root), one("name") or os.path.basename(path), replace)


class GameFiles:
    def __init__(self, game=None, user=None, mods=None):
        self.game = game or game_dir()
        self.user = user or user_dir()
        self.mods = self._active_mods() if mods is None else mods
        self._listing = {}

    def _active_mods(self):
        try:
            load = json.load(open(os.path.join(self.user, "dlc_load.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return []
        mods = []
        for rel in load.get("enabled_mods", []):
            d = os.path.join(self.user, rel)
            if os.path.exists(d):
                m = _parse_descriptor(d, self.user)
                if m.root and os.path.isdir(m.root):
                    mods.append(m)
        return mods

    def _layers(self, folder):
        """Roots that can supply files of FOLDER, lowest priority first."""
        folder = _norm(folder)
        layers = [self.game]
        for m in self.mods:
            if folder in m.replace:
                layers = []
            layers.append(m.root)
        return layers

    def find(self, rel):
        """Full path of the file the game uses for REL (e.g. 'map/definition.csv'), or None."""
        folder = os.path.dirname(_norm(rel))
        for root in reversed(self._layers(folder)):
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                return p
        return None

    def listdir(self, folder, ext=None):
        """{file name: full path} of the files the game uses in FOLDER (later layers override same names)."""
        key = (_norm(folder), ext)
        if key not in self._listing:
            files = {}
            for root in self._layers(folder):
                d = os.path.join(root, folder)
                if os.path.isdir(d):
                    for n in os.listdir(d):
                        p = os.path.join(d, n)
                        if os.path.isfile(p) and (ext is None or n.lower().endswith(ext)):
                            files[n.lower()] = p
            self._listing[key] = files
        return dict(self._listing[key])

    def describe(self):
        return "game %s, %d mods%s" % (self.game, len(self.mods),
                                       "".join("\n  %s (%s)" % (m.name, m.root) for m in self.mods))
