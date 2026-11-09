"""Read the running game's memory (read-only, ReadProcessMemory), and name objects by their RTTI class.

Objects are found from the game state (CCurrentGameState), whose address the exe keeps in a static. Its offset
can move with a patch, so a guess is only used when RTTI confirms it; otherwise the object is found by its
class name (a scan of the heap for its vtable, a few seconds).
"""
import ctypes
import ctypes.wintypes as W
import struct
import subprocess

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64), ("AllocationProtect", W.DWORD),
                ("PartitionId", W.WORD), ("RegionSize", ctypes.c_uint64), ("State", W.DWORD), ("Protect", W.DWORD),
                ("Type", W.DWORD)]


class _ME32(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD), ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                ("ProccntUsage", W.DWORD), ("modBaseAddr", ctypes.c_uint64), ("modBaseSize", W.DWORD),
                ("hModule", ctypes.c_void_p), ("szModule", ctypes.c_char * 256), ("szExePath", ctypes.c_char * 260)]


k32.OpenProcess.restype = ctypes.c_void_p
k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(_MBI), ctypes.c_size_t]
k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
k32.Module32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ME32)]
k32.CloseHandle.argtypes = [ctypes.c_void_p]

MEM_COMMIT, MEM_PRIVATE, MEM_IMAGE = 0x1000, 0x20000, 0x1000000
READABLE = (0x02, 0x04, 0x08, 0x20, 0x40)
GAMESTATE_STATIC_RVA = 0x33dd6b0  # 1.19.4 and its hotfix; only a hint


class GameNotRunning(RuntimeError):
    pass


def find_pid(name="hoi4.exe"):
    out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq " + name, "/FO", "CSV", "/NH"],
                                  creationflags=0x08000000).decode("latin-1")
    try:
        return int(out.split(",")[1].strip('"'))
    except (IndexError, ValueError):
        raise GameNotRunning(name + " is not running")


class Process:
    def __init__(self, pid=None):
        self.pid = pid or find_pid()
        self.h = k32.OpenProcess(0x0410, False, self.pid)  # QUERY_INFORMATION | VM_READ
        if not self.h:
            raise GameNotRunning("cannot open process %d (error %d)" % (self.pid, ctypes.get_last_error()))
        snap = k32.CreateToolhelp32Snapshot(0x18, self.pid)  # TH32CS_SNAPMODULE | SNAPMODULE32
        me = _ME32()
        me.dwSize = ctypes.sizeof(me)
        if not k32.Module32First(snap, ctypes.byref(me)):
            raise GameNotRunning("cannot list modules of process %d" % self.pid)
        self.base, self.size = me.modBaseAddr, me.modBaseSize
        k32.CloseHandle(snap)
        self._cls = {}

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, addr, buf, n, ctypes.byref(got)) or got.value != n:
            return None
        return buf.raw

    def u64(self, addr):
        b = self.read(addr, 8)
        return struct.unpack("<Q", b)[0] if b else 0

    def i32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def in_image(self, v):
        return self.base <= v < self.base + self.size

    def class_of(self, obj):
        """RTTI class name of the object at OBJ (its first word is a vtable), or None."""
        vt = self.u64(obj)
        if not self.in_image(vt):
            return None
        if vt in self._cls:
            return self._cls[vt]
        name = None
        col = self.u64(vt - 8)
        c = self.read(col, 0x18) if self.in_image(col) else None
        if c:
            td = struct.unpack_from("<i", c, 12)[0]
            raw = self.read(self.base + td + 0x10, 128)
            if raw:
                name = raw.split(b"\0")[0].decode("latin-1")
                for pre in (".?AV", ".?AU"):
                    if name.startswith(pre):
                        name = name[len(pre):]
                if name.endswith("@@"):
                    name = name[:-2]
        self._cls[vt] = name
        return name

    def regions(self, types=(MEM_PRIVATE,)):
        a, m = 0, _MBI()
        while a < 0x7fffffffffff and k32.VirtualQueryEx(self.h, a, ctypes.byref(m), ctypes.sizeof(m)):
            if m.State == MEM_COMMIT and m.Protect in READABLE and m.Type in types:
                yield m.BaseAddress, m.RegionSize
            a = m.BaseAddress + m.RegionSize

    def search(self, pat, types=(MEM_PRIVATE,), align=1):
        chunk = 1 << 24
        for base, size in self.regions(types):
            for off in range(0, size, chunk):
                b = self.read(base + off, min(chunk + len(pat), size - off))
                if not b:
                    continue
                i = b.find(pat)
                while i >= 0:
                    if (base + off + i) % align == 0:
                        yield base + off + i
                    i = b.find(pat, i + 1)

    def vtable_of(self, cls_name):
        """Address of the primary vtable of class CLS_NAME (from its RTTI), or None."""
        image = self.read(self.base, self.size)
        if not image:
            return None
        tname = (".?AV%s@@" % cls_name).encode()
        i = image.find(tname + b"\0")
        if i < 0:
            return None
        td_rva = i - 0x10
        pos = 0
        while True:
            j = image.find(struct.pack("<i", td_rva), pos)
            if j < 0:
                return None
            pos = j + 1
            col = j - 12
            sig, off = struct.unpack_from("<ii", image, col)
            if sig != 1 or off != 0:
                continue
            k = image.find(struct.pack("<Q", self.base + col))
            if k >= 0 and k % 8 == 0:
                return self.base + k + 8

    def find_objects(self, cls_name):
        vt = self.vtable_of(cls_name)
        return [] if vt is None else list(self.search(struct.pack("<Q", vt), align=8))

    def gamestate(self):
        g = self.u64(self.base + GAMESTATE_STATIC_RVA)
        if g and self.class_of(g) == "CCurrentGameState":
            return g
        objs = self.find_objects("CCurrentGameState")
        if not objs:
            raise RuntimeError("CCurrentGameState not found")
        return objs[0]
