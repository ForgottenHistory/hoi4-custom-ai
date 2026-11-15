"""The international market: every purchase contract, and what countries put up for sale.

MarketLog(path, names, tags, others).step(date, lines) writes to market.tsv:
  date  change  kind  id  seller  buyer  designs  names  amounts  factories  cost
kind contract: a purchase contract (handle 81, id its id) of any two countries, read from the game objects; designs
the equipment design ids it buys, names their names (the equipment type for an unnamed one), amounts how many of each, factories the civilian factories paying for it, cost the
total price. kind listing: equipment a country (seller) has on the market, id the design id, from the market_stockpile
of its save-format block. change: start (at the first read), new, changed, removed (a contract ends when delivered or
cancelled; a listing when sold out or taken back). A contract read in the hour it is made has factories 0 (the game
fills them in later); the next read writes it changed with the factories.

Reads at the first call, each new in-game day and whenever the record LINES hold a market command: contracts always,
listings of TAGS (the recorded countries) each time and of OTHERS() (a callable, e.g. MioLog's producing countries)
every FULL_DAYS days.

The player's commands (in command_record.tsv):
  CMarketStockpileEquipmentTransferCommand@NInternationalMarket   putting equipment up for sale, changing the amount
      or taking it back: vectors of { CEquipmentVariant*, amount * 100000 } at +0x48 (to the market) and +0x88 (back
      from it); install_rules has the bridge write the first element of each (D+0x48=CEquipmentVariant.0.0xc=ID,
      D+0x48=?.0x8=AMOUNT; needs a bridge with deref paths)
  CDiplomaticActionCommand with P+0x28=CRequestEquipmentPurchaseAction@NInternationalMarket   buying (the contract
      it makes shows up here the same day)
A CPurchaseContract@NInternationalMarket (bridge.object 81 ID): seller country id +0x58, buyer +0x5c, total cost * 100000
+0xe8, factories +0x260; vectors (data, int count) of { CEquipmentVariant*, int * 100000 }: unit cost at +0x38, amount at
+0x80 (cost = sum of amount * unit cost * 2 in every contract seen).
"""
import os
import re

from hoi4ai import shared
from hoi4bridge import send

FULL_DAYS = 7
HEADER = "date\tchange\tkind\tid\tseller\tbuyer\tdesigns\tnames\tamounts\tfactories\tcost"
TRANSFER = "CMarketStockpileEquipmentTransferCommand@NInternationalMarket"
CLASSES = (TRANSFER, "CMarketStockpileClearCommand@NInternationalMarket", "CRequestEquipmentPurchaseAction")
_LISTING = re.compile(r"equipment=\{\s*id=\{ id=(\d+) type=70 \}\s*amount=(\d+)")


def install_rules():
    """Have bridge.cmdrec write the design and amount of the first element of a transfer's two vectors."""
    send(f"bridge.cmdrec rules {TRANSFER} off")
    ok = True
    for off in (0x48, 0x88):
        for field in ("0.c", "8"):
            ok = send(f"bridge.cmdrec deref {TRANSFER} {off:#x} {field}")[0].startswith("OK") and ok
    return ok


def _fmt(v):
    return f"{v / 100000:g}"


class MarketLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        from hoi4ai.memory import Process
        from hoi4ai.templates import Reader
        self.proc = Process()
        self.reader = Reader(self.proc)
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.last = None            # (kind, id, seller) -> row values
        self.day, self.days = None, 0
        self.listings = {}          # tag -> {(kind, id, seller): values}, kept between full reads
        self.design = {}            # design id -> name
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def _variant(self, v):
        did = self.proc.i32(v + 0xc)
        if did not in self.design:
            var = self.reader.variant(v) if self.proc.class_of(v) == "CEquipmentVariant" else None
            self.design[did] = (var.name or var.type) if var else ""   # unnamed base designs: the equipment type
        return did, self.design[did]

    def _vector(self, a):
        """[(variant address, int)] of a contract vector at A."""
        p, n = self.proc.u64(a), self.proc.i32(a + 8)
        return [(self.proc.u64(p + 16 * k), self.proc.i32(p + 16 * k + 8)) for k in range(max(0, min(n, 64)))]

    def contracts(self, max_gap=50):
        p, out, i, last = self.proc, {}, 1, 0
        while i - last <= max_gap:
            ids = range(i, i + 50)
            for k, r in zip(ids, send(*[f"bridge.object 81 {j}" for j in ids])):
                m = re.search(r"@0x([0-9a-fA-F]+)", r) if r.startswith("OK") and "PurchaseContract" in r else None
                if not m:
                    continue
                last, a = k, int(m.group(1), 16)
                items = [self._variant(v) + (n,) for v, n in self._vector(a + 0x80)]
                out[("contract", str(k), self.names.tag(p.i32(a + 0x58)))] = (
                    self.names.tag(p.i32(a + 0x5c)), ",".join(str(d) for d, _, _ in items),
                    ",".join(nm for _, nm, _ in items), ",".join(_fmt(n) for _, _, n in items),
                    str(p.i32(a + 0x260)), _fmt(p.i32(a + 0xe8)))
            i += 50
        return out

    def listing(self, tag):
        r = shared.country(tag)
        text = "\n".join(r) if r and r[0].startswith("OK") else ""
        m = re.search(r"market_stockpile=\{(.*?)allow_zero_entries", text, re.S)
        out = {}
        for did, n in _LISTING.findall(m.group(1) if m else ""):
            a = re.search(r"@0x([0-9a-fA-F]+)", send(f"bridge.object 70 {did}")[0])
            name = self._variant(int(a.group(1), 16))[1] if a else ""
            out[("listing", did, tag)] = ("", did, name, n, "", "")
        return out

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        if self.last is not None and day == self.day and not any(c in ln for ln in lines for c in CLASSES):
            return 0
        if day != self.day:
            self.days += 1
        self.day = day
        todo = list(self.tags)
        others = (self.others() or ()) if self.last is None else shared.part(self.others(), self.days, FULL_DAYS)
        todo += [t for t in others if t not in self.tags]
        for t in todo:
            self.listings[t] = self.listing(t)
        now = self.contracts()
        for v in self.listings.values():
            now.update(v)
        if self.last is None:
            rows = [("start", k, v) for k, v in sorted(now.items())]
        else:
            rows = [("new" if k not in self.last else "changed", k, v) for k, v in sorted(now.items())
                    if self.last.get(k) != v]
            rows += [("removed", k, self.last[k]) for k in sorted(self.last) if k not in now]
        self.last = now
        for change, (kind, i, seller), v in rows:
            self.f.write("\t".join((date, change, kind, i, seller) + v) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
