"""Input-level APM: how many keys and mouse buttons the player presses, per second, while HOI4 is the focused window.

OFF unless asked for (cmdrec --input-apm). It watches the machine's input, not the game, so it is narrow on purpose:
  - it keeps COUNTS only. Which key, what was typed, where the pointer was: none of it is read into the log, held or
    written. A press is an edge (up to down) of any key or mouse button, counted as "keys" or "clicks";
  - it counts only while the foreground window belongs to a process in PROCESSES (hoi4.exe), so typing elsewhere is
    never counted;
  - modifier keys (shift, ctrl, alt, win) are not counted, so ctrl+click is one press, not two;
  - it writes one row for each second with a press: nothing for idle seconds.

InputLog(path, origin_tick=None).start() / .stop() writes input.tsv:
  #start <unix seconds> <origin tick>   (first line)
  second  keys  clicks
second counts from ORIGIN_TICK, a GetTickCount64 value (default: when the log starts). cmdrec passes the tick at which
it turned bridge.cmdrec on, and the bridge stamps every record line with GetTickCount64() minus its own start, so
second*1000 is on the same clock as the `ms` column of command_record.tsv (to within the round trip, a few ms) and
apm.py can put presses and commands in the same minute.
Polls GetAsyncKeyState (no hook, no driver, no extra module). Windows only; elsewhere start() says so and does nothing.
Pointer movement, the wheel and holding a key down are not presses, so this under-counts a camera pan and over-counts
nothing.
"""
import ctypes
import os
import sys
import threading
import time

PROCESSES = ("hoi4.exe",)
CLICKS = (0x01, 0x02, 0x04, 0x05, 0x06)          # left, right, middle, X1, X2
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C) + tuple(range(0xA0, 0xA6))
KEYS = tuple(v for v in range(0x08, 0xFF) if v not in CLICKS and v not in MODIFIERS)
POLL = 0.01


def foreground_process():
    """The lower-case exe name of the foreground window's process, or ""."""
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = ctypes.c_ulong()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.c_ulong(len(buf))
        return os.path.basename(buf.value).lower() if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        k.CloseHandle(h)


class InputLog:
    def __init__(self, path, processes=PROCESSES, origin_tick=None):
        self.path, self.processes = path, tuple(p.lower() for p in processes)
        self.origin_tick = origin_tick
        self.stop_event = threading.Event()
        self.thread = None
        self.total = [0, 0]

    def start(self):
        if sys.platform != "win32":
            print("input APM: Windows only, not recording it")
            return False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        return tuple(self.total)

    def _run(self):
        get = ctypes.windll.user32.GetAsyncKeyState
        down = {v: False for v in KEYS + CLICKS}
        tick = ctypes.windll.kernel32.GetTickCount64
        tick.restype = ctypes.c_uint64
        begin_tick = self.origin_tick if self.origin_tick is not None else tick()
        begin = time.time() - (tick() - begin_tick) / 1000
        with open(self.path, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write(f"#start\t{begin:.3f}\t{begin_tick}\nsecond\tkeys\tclicks\n")
            sec, keys, clicks, since, focused = None, 0, 0, 0.0, False
            while not self.stop_event.is_set():
                now = time.time()
                focused = foreground_process() in self.processes if now - since > 0.2 else focused
                since = now if now - since > 0.2 else since
                presses = [0, 0]
                for v in down:
                    d = bool(get(v) & 0x8000)
                    if d and not down[v] and focused:
                        presses[v in CLICKS] += 1
                    down[v] = d
                s = int((tick() - begin_tick) // 1000)
                if s != sec:
                    if sec is not None and (keys or clicks):
                        f.write(f"{sec}\t{keys}\t{clicks}\n")
                        f.flush()
                    sec, keys, clicks = s, 0, 0
                keys, clicks = keys + presses[0], clicks + presses[1]
                self.total[0] += presses[0]
                self.total[1] += presses[1]
                self.stop_event.wait(POLL)
            if keys or clicks:
                f.write(f"{sec}\t{keys}\t{clicks}\n")
