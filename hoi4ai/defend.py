"""Running a defence, after how a player defends (Poland against Germany, 1939):

- Hold one line (hoi4ai/defence.py picks it: the most victory points kept, with a buffer above the surrender limit,
  for the divisions we have; behind rivers and in rough ground where it can). Land in front of it is given up.
- Divisions go along the line by what each province needs: more where the attack is easy (open ground, no river,
  a wide battle), fewer behind a river or in mountains. Divisions already holding stay where they are.
- A reserve (the fastest divisions) waits behind the line, near the sectors that need the most, to plug breaches:
  a province behind the line taken by a weak enemy is attacked back; against a strong one the reserve blocks
  around it.
- When the line breaks (a share of its provinces lost, or the victory points behind it fall under the buffer),
  a new line is drawn on the land still held: falling back, behind the next river if there is one.

Pure logic (no game calls). plan() returns {division id: province} and a report; self.view holds what it saw.
"""
import collections
import math

from .front import power


class Settings:
    reserve_share = 0.2     # of our divisions kept back to plug breaches
    max_stack = 6           # divisions per line province at most (more starve: supply)
    broken_share = 0.25     # the line is broken when this share of its provinces is the enemy's
    counter_margin = 1.5    # reserves attack a lost province when their power is this many times the enemy's there
    replan_hours = 24       # at most one new line this often (unless forced)


class Defender:
    def __init__(self, m, analysis, me, speeds=None, settings=None):
        """ANALYSIS a defence.Defence (forces set); SPEEDS {template: km/h} to pick the reserve (fastest first)."""
        self.m, self.d, self.me = m, analysis, me
        self.speeds = speeds or {}
        self.s = settings or Settings()
        self.line = self.fallback = None
        self.lines_seen = 0
        self._planned_at = -math.inf
        self.stops = []
        self.view = {}

    def nb(self, p):
        return self.m.land_neighbours(p)

    def hops(self, sources, allowed):
        """Steps from SOURCES to each province of ALLOWED, through ALLOWED."""
        dist = {p: 0 for p in sources}
        todo = collections.deque(sources)
        while todo:
            p = todo.popleft()
            for n in self.nb(p):
                if n in allowed and n not in dist:
                    dist[n] = dist[p] + 1
                    todo.append(n)
        return dist

    def broken(self, ctrl, attackers):
        if self.line is None:
            return True
        lost = sum(1 for p in self.line.front if ctrl[p] != self.me)
        if lost > self.s.broken_share * len(self.line.front):
            return True
        floor = 1 - self.d.r.surrender_limit
        held = sum(v for p, v in self._vps.items() if p in self.line.keep and ctrl[p] == self.me)
        return self.line.vp > floor + self.d.s.buffer and held / self._total_vp < floor + self.d.s.buffer

    def plan(self, ctrl, owner, divisions, my_tag, attackers, at_war, foes, div_width, foe_width, hour=0):
        """CTRL / OWNER: country id per province. ATTACKERS: country ids that attack us (or will). FOES: their
        divisions. HOUR: hours since some start (for replan_hours)."""
        m, s = self.m, self.s
        self.stops = []
        mine = [d for d in divisions if d.owner == my_tag and m.is_land(d.province)]
        own = {p for p in m.provinces if m.is_land(p) and ctrl[p] == self.me}
        self._vps = {p: v for st in m.states.values() for p, v in st.victory_points.items()
                     if m.is_land(p) and owner[p] == self.me}
        self._total_vp = sum(self._vps.values()) or 1.0
        n_foes = len(foes)
        replanned = False
        if self.broken(ctrl, attackers) and (self.line is None or hour - self._planned_at >= s.replan_hours
                                             or not (self.line.keep & own)):
            lines = self.d.lines(ctrl, owner, attackers, div_width, n_foes, foe_width)
            if lines:
                self.line, self.fallback = self.d.choose(lines, len(mine))
                self._planned_at = hour
                self.lines_seen += 1
                replanned = True
        if self.line is None:
            return {}, {"divisions": len(mine), "front": 0}
        line = self.line
        keep = line.keep & own
        front = [p for p in line.front if ctrl[p] == self.me] or sorted(keep)[:1]
        per = n_foes / max(1, len(front))
        need = {p: self.d.need(p, line.approaches.get(p) or [q for q in self.nb(p) if q not in keep] or [p],
                               div_width, per, foe_width) for p in front}

        # who is where: a division counts where it is going (keeps orders from flapping)
        at = {d.id: (d.dest if d.moving and d.dest else d.province) for d in mine}
        by_speed = sorted(mine, key=lambda d: -self.speeds.get(d.template, 0))
        n_res = int(round(len(mine) * s.reserve_share))
        reserve_ids = {d.id for d in by_speed[:n_res]}

        # line slots: by need, at least one each, at most max_stack (largest remainder)
        n_line = len(mine) - n_res
        total = sum(need.values()) or 1.0
        raw = {p: n_line * need[p] / total for p in front}
        alloc = {p: max(1, min(s.max_stack, int(raw[p]))) for p in front}
        spare = n_line - sum(alloc.values())
        for p in sorted(front, key=lambda p: -(raw[p] - int(raw[p]))):
            if spare <= 0:
                break
            if alloc[p] < s.max_stack:
                alloc[p] += 1
                spare -= 1

        # reserve points: behind the line, near the sectors that need the most (one per few reserve divisions)
        inner = keep - set(front)
        k = max(1, min(4, n_res // 3)) if n_res else 0
        points = []
        if k and inner:
            sectors = self._sectors(front, need, k)
            for sec in sectors:
                cx = sum(m.provinces[p].x * need[p] for p in sec) / (sum(need[p] for p in sec) or 1)
                cy = sum(m.provinces[p].y * need[p] for p in sec) / (sum(need[p] for p in sec) or 1)
                near = self.hops(sec, keep)
                cand = [p for p in inner if near.get(p, 9) <= 2] or list(inner)
                points.append(min(cand, key=lambda p: math.hypot(m.provinces[p].x - cx, m.provinces[p].y - cy)
                                  - 20 * (m.rails.get(p, 0) + m.hubs.get(p, 0))))

        orders, used = {}, set()
        dist_cache = {}

        def dist(d, p):
            if p not in dist_cache:
                dist_cache[p] = self.hops([p], own)
            h = dist_cache[p].get(at[d.id])
            if h is not None:
                return h
            a, b = m.provinces.get(at[d.id]), m.provinces[p]
            return 100 + (math.hypot(a.x - b.x, a.y - b.y) / 20 if a else 999)

        # breaches: land behind the line the enemy took. Reserves attack it back when strong enough, else block
        foe_power = collections.Counter()
        for x in foes:
            if m.is_land(x.province):
                foe_power[x.province] += power(x)
        lost = [p for p in line.keep if ctrl[p] in attackers]
        counters = blocks = 0
        reserves = [d for d in mine if d.id in reserve_ids]
        if at_war and lost:
            for p in sorted(lost, key=lambda p: -need.get(p, 0) - self._vps.get(p, 0)):
                free = sorted((d for d in reserves if d.id not in used), key=lambda d: dist(d, p))
                if not free:
                    break
                have = 0.0
                team = []
                for d in free[:s.max_stack]:
                    team.append(d)
                    have += power(d)
                    if have >= s.counter_margin * foe_power[p] and len(team) >= 2:
                        break
                if have >= s.counter_margin * foe_power[p]:
                    for d in team:
                        orders[d.id] = p
                        used.add(d.id)
                    counters += 1
                else:  # too strong: block the provinces next to it on our side
                    for q in [q for q in self.nb(p) if q in own and q in line.keep][:2]:
                        free = [d for d in reserves if d.id not in used]
                        if free:
                            d = min(free, key=lambda d: dist(d, q))
                            orders[d.id] = q
                            used.add(d.id)
                            blocks += 1

        # line holders: those already on a line province stay (up to its slots), then the nearest
        slots = collections.Counter(alloc)
        for d in sorted(mine, key=lambda d: d.id in reserve_ids):
            p = at[d.id]
            if d.id not in used and slots.get(p, 0) > 0:
                orders[d.id] = p
                used.add(d.id)
                slots[p] -= 1
        pairs = sorted((dist(d, p), d.id in reserve_ids, d.id, p) for d in mine if d.id not in used
                       for p in slots if slots[p] > 0)
        by_id = {d.id: d for d in mine}
        for _, _, did, p in pairs:
            if did in used or slots[p] <= 0:
                continue
            if did in reserve_ids and sum(1 for d in mine if d.id not in used and d.id not in reserve_ids) >= \
                    sum(slots.values()):
                continue  # line divisions are enough: keep the reserve
            orders[did] = p
            used.add(did)
            slots[p] -= 1
        # the reserve (and anyone left) to the reserve points
        for d in sorted((d for d in mine if d.id not in used), key=lambda d: -self.speeds.get(d.template, 0)):
            if points:
                p = min(points, key=lambda p: dist(d, p))
                orders[d.id] = p
            else:
                orders[d.id] = min(front, key=lambda p: dist(d, p))
            used.add(d.id)

        self.view = dict(line=line, front=front, need=need, alloc=alloc, points=points, lost=lost)
        rep = dict(divisions=len(mine), front=len(front), line_vp=round(line.vp, 3),
                   line_need=round(sum(need.values())), reserve=n_res, reserve_points=len(points),
                   lost_behind=len(lost), counters=counters, blocks=blocks, replanned=int(replanned),
                   lines=self.lines_seen, unfilled=sum(slots.values()),
                   held=sum(1 for d in mine if at[d.id] in alloc and d.province == at[d.id]),
                   short_of_supply=sum(1 for d in mine if getattr(d, "supply", 100000) < 95000))
        return orders, rep

    def _sectors(self, front, need, k):
        """FRONT split into K stretches of about equal need, along the line (ordered by walking it)."""
        left = set(front)
        order = []
        while left:  # walk the line from an end: start where fewest line neighbours, go to the nearest next
            start = min(left, key=lambda p: (sum(1 for q in self.nb(p) if q in left), p))
            cur = start
            while cur is not None:
                order.append(cur)
                left.discard(cur)
                nxt = [q for q in self.nb(cur) if q in left]
                if not nxt and left:
                    c = self.m.provinces[cur]
                    q = min(left, key=lambda q: math.hypot(self.m.provinces[q].x - c.x, self.m.provinces[q].y - c.y))
                    nxt = [q] if math.hypot(self.m.provinces[q].x - c.x, self.m.provinces[q].y - c.y) < 60 else []
                cur = nxt[0] if nxt else None
        total = sum(need.values()) or 1.0
        out, acc, cur = [], 0.0, []
        for p in order:
            cur.append(p)
            acc += need[p]
            if acc >= total / k * (len(out) + 1) and len(out) < k - 1:
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        return out
