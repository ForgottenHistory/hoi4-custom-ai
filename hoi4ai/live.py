"""Live province data from the running game: who controls and who owns each province, and its state.

Layouts (game 1.19.x, verified 2026-11-09): CCurrentGameState holds a vector of CProvince* indexed by province id
(at +0x2b0 in 1.19.4; found again by content if it moves). CProvince: id +0xa4, CState* +0xc0, controller country
id +0x188. CState: id +0x58, owner +0xc8, controller +0xcc. Country ids are the game's (bridge.tag names them).
"""
import struct

import numpy as np

from .memory import Process

PROV_ID, PROV_STATE, PROV_CONTROLLER = 0xa4, 0xc0, 0x188
STATE_ID, STATE_OWNER, STATE_CONTROLLER = 0x58, 0xc8, 0xcc
PROVINCE_TABLE_HINT = 0x2b0


def _vector(proc, addr):
    b = proc.read(addr, 16)
    if not b:
        return None
    ptr, cap, count = struct.unpack("<Qii", b)
    return ptr, cap, count


class Provinces:
    """controller / owner / state of every province, read in one pass (numpy arrays indexed by province id)."""

    def __init__(self, proc=None):
        self.proc = proc or Process()
        self.gamestate = self.proc.gamestate()
        self.table, self.count = self._find_table()
        self.addr = np.frombuffer(self.proc.read(self.table, 8 * self.count), np.uint64).copy()
        self.controller = self.owner = self.state = None
        self._state_ptr = None

    def _is_table(self, off):
        v = _vector(self.proc, self.gamestate + off)
        if not v:
            return None
        ptr, cap, count = v
        if not (1000 < count <= cap < 1 << 20) or not ptr:
            return None
        for i in (1, 2, count - 1):
            p = self.proc.u64(ptr + 8 * i)
            if not p or self.proc.i32(p + PROV_ID) != i or self.proc.class_of(p) != "CProvince":
                return None
        return ptr, count

    def _find_table(self):
        t = self._is_table(PROVINCE_TABLE_HINT)
        if t:
            return t
        for off in range(0, 0x4000, 8):
            t = self._is_table(off)
            if t:
                return t
        raise RuntimeError("province table not found in CCurrentGameState")

    def _read_fields(self, addrs, fields):
        """{field offset: int32 array} for each object address (0 = missing), reading nearby objects together."""
        out = {f: np.zeros(len(addrs), np.int32) for f in fields}
        span = max(fields) + 4
        order = np.argsort(addrs)
        i, n = 0, len(order)
        while i < n:
            a0 = int(addrs[order[i]])
            if a0 == 0:
                i += 1
                continue
            j = i
            while j + 1 < n and int(addrs[order[j + 1]]) - a0 + span <= 1 << 20 \
                    and int(addrs[order[j + 1]]) - int(addrs[order[j]]) < 0x10000:
                j += 1
            end = int(addrs[order[j]]) + span
            buf = self.proc.read(a0, end - a0)
            for k in range(i, j + 1):
                idx = order[k]
                off = int(addrs[idx]) - a0
                for f in fields:
                    if buf is not None:
                        out[f][idx] = struct.unpack_from("<i", buf, off + f)[0]
                    else:
                        out[f][idx] = self.proc.i32(int(addrs[idx]) + f) or 0
            i = j + 1
        return out

    def refresh(self):
        """Re-read controllers and owners (a few ms)."""
        pf = self._read_fields(self.addr, (PROV_CONTROLLER,))
        self.controller = pf[PROV_CONTROLLER]
        if self._state_ptr is None:  # provinces don't change state during a game
            self._state_ptr = np.zeros(self.count, np.uint64)
            for i in range(1, self.count):
                if self.addr[i]:
                    self._state_ptr[i] = self.proc.u64(int(self.addr[i]) + PROV_STATE)
            self._states = np.unique(self._state_ptr[self._state_ptr != 0])
            sid = self._read_fields(self._states, (STATE_ID,))[STATE_ID]
            self._state_index = {int(p): k for k, p in enumerate(self._states)}
            self.state = np.array([sid[self._state_index[int(p)]] if p else 0 for p in self._state_ptr], np.int32)
            self._prov_state_idx = np.array([self._state_index.get(int(p), -1) for p in self._state_ptr])
        sf = self._read_fields(self._states, (STATE_OWNER,))
        so = sf[STATE_OWNER]
        self.owner = np.where(self._prov_state_idx >= 0, so[self._prov_state_idx.clip(0)], 0).astype(np.int32)
        return self
