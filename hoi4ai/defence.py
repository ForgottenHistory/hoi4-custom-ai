"""Where to defend: lines a country could hold against an attacker, and what each would keep.

After how a player defends:
- Victory points decide capitulation: the country surrenders once enemies control the surrender limit's share of
  its victory points (BASE_SURRENDER_LIMIT, 80% in the base game). Keep enough, with a buffer in the bank.
- Factories count as well: the ones behind the line keep producing.
- Hold where attacking is hard: behind rivers, in hills, mountains, forests, cities, on forts. Every one of these
  cuts the attacker's attack (the game's common/terrain and defines), so fewer divisions hold there.
- A battle only takes so many divisions (the terrain's combat width; wider for each extra direction attacked
  from). Divisions beyond that wait in reserve and join as others tire, so a stack helps only up to a point,
  and costs supply beyond it. A province's need grows with the width the attacker can use.
- A short line is cheaper than a long one: falling back behind a river can take fewer divisions than holding
  the border.

How: a line is the edge of the land we keep. Holding a province costs divisions for every side the attacker can
come from, fewer behind a river or in rough ground; giving a province up costs its worth (victory points,
factories, a little for the land). For a price on land from cheap to dear, the cheapest split is a minimum cut, so
lines bend along rivers and hills by themselves: from a last stand around the capital to the whole border. Each
candidate is rated by the victory points and factories it keeps, its provinces, and the divisions it needs
against the divisions we have. reach(): the attacker's cost to get to each province (1 / attack multiplier per
step), how hard a victory point is to take.

Pure logic (no game calls); tools/defence.py gathers the live data and draws the result.
"""
import collections
import heapq
import math

from . import pdx
from .gamefiles import GameFiles

DIVISION_WIDTH = 18


class Settings:
    hold = 2.0            # (the cut's price of holding a plains province from one side, in divisions)
    defender_edge = 1.0   # a line holds while it takes no more than this x the damage it deals, per division
                          # (without both armies' stats: attackers one defender holds off, division for division)
    rotation = 0.3        # divisions in reserve per division fighting, to swap in as they tire
    reserve = 1.5         # a province uses up to this many times the divisions its combat width takes at once
    buffer = 0.15         # victory points to keep beyond the surrender limit (share of all of them)
    min_attack = 0.1      # floor on an attack multiplier (penalties don't take an attack to nothing)
    edge_share = 0.6      # in the cut, each direction a province is attacked from costs this share of its need
    factory_worth = 0.5   # all factories are worth this share of all victory points
    land_worth = 0.2      # all land (bare provinces) is worth this share of all victory points
    prices, price_min, price_max = 24, 1.0, 2000.0
    depths = 12          # depths tried for lines at least so deep (quantiles of the attacker's reach)   # the prices on land tried, in divisions per all our worth


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class Rules:
    """The game's numbers for attacking: per terrain its attack modifier, movement cost and combat widths; river
    and fort penalties; the surrender limit. Read from the game files (mods included)."""

    def __init__(self, files=None):
        files = files or GameFiles()
        self.attack, self.width = {}, {}
        for _, path in sorted(files.listdir("common/terrain", ".txt").items()):
            tree = pdx.parse(open(path, encoding="utf-8-sig", errors="replace").read())
            for name, _, body in pdx.get(tree, "categories", []) or []:
                if not isinstance(body, list) or pdx.get(body, "is_water") == "yes":
                    continue
                units = pdx.get(body, "units") or []
                self.attack[name] = _num(pdx.get(units, "attack") if isinstance(units, list) else 0)
                if pdx.get(body, "combat_width") is not None:
                    self.width[name] = (_num(pdx.get(body, "combat_width")),
                                        _num(pdx.get(body, "combat_support_width")))
        d = self.defines(files)
        self.river = {0: 0.0, 1: d.get("RIVER_CROSSING_PENALTY", -0.3), 2: d.get("RIVER_CROSSING_PENALTY_LARGE", -0.6)}
        self.fort = d.get("BASE_FORT_PENALTY", -0.15)
        self.surrender_limit = d.get("BASE_SURRENDER_LIMIT", 0.8)

    @staticmethod
    def defines(files):
        import re
        out = {}
        for _, path in sorted(files.listdir("common/defines", ".lua").items()):
            for k, v in re.findall(r"(?m)^\s*(\w+)\s*=\s*(-?[\d.]+)", open(path, encoding="utf-8", errors="replace")
                                   .read()):
                out[k] = float(v)
        return out


def buildings(m):
    """(forts {province: land fort level}, factories {state id: civilian + military factories}) from the states'
    history files (their starting buildings)."""
    forts, factories = {}, {}
    for st in m.states.values():
        try:
            tree = pdx.parse(open(st.file, encoding="utf-8-sig", errors="replace").read())
        except OSError:
            continue
        state = pdx.get(tree, "state") or []
        hist = pdx.get(state, "history") or []
        b = pdx.get(hist, "buildings") or [] if isinstance(hist, list) else []
        if not isinstance(b, list):
            continue
        factories[st.id] = _num(pdx.get(b, "industrial_complex")) + _num(pdx.get(b, "arms_factory"))
        for k, _, v in b:
            if isinstance(k, str) and k.isdigit() and isinstance(v, list):
                lvl = _num(pdx.get(v, "bunker"))
                if lvl:
                    forts[int(k)] = lvl
    return forts, factories


class Line:
    """A line: KEEP the land behind it (FRONT its provinces facing the attacker), VP / FACTORIES the shares kept,
    NEED the divisions it takes, APPROACHES {front province: provinces the attacker comes from}, T the depth it
    was found at (inf: by price alone)."""

    def __init__(self, t, keep, front, vp, factories, need, approaches):
        self.t, self.keep, self.front = t, keep, front
        self.vp, self.factories, self.need = vp, factories, need
        self.approaches = approaches  # {front province: [provinces the attacker comes from]}
        self.front_vp = 0.0  # share of the victory points in its most valuable front province


class Defence:
    """M the map, RULES the game's numbers, ME our country id. ctrl / owner: country id per province."""

    def __init__(self, m, rules, me, forts=None, factories=None, settings=None):
        self.m, self.r, self.me = m, rules, me
        self.forts, self.factories = forts or {}, factories or {}
        self.s = settings or Settings()
        self.state_of = {p: st.id for st in m.states.values() for p in st.provinces}
        self.forces = None

    def nb(self, p):
        return self.m.land_neighbours(p)

    def attack_mult(self, src, dst):
        """How much of its attack an attacker keeps going from SRC into DST (terrain, river, forts)."""
        ter = self.m.provinces[dst].terrain
        f = (1 + self.r.attack.get(ter, 0.0)) * (1 + self.r.river[self.m.river(src, dst)])
        f *= 1 + self.r.fort * self.forts.get(dst, 0)
        return max(self.s.min_attack, f)

    def battle_width(self, p, directions):
        w, extra = self.r.width.get(self.m.provinces[p].terrain, (70.0, 35.0))
        return w + extra * max(0, directions - 1)

    def reach(self, ctrl, attackers, own):
        """The attacker's cost to reach each province of OWN from the land ATTACKERS control (inf: no way in)."""
        dist, heap = {}, []
        for p in own:
            for q in self.nb(p):
                if ctrl[q] in attackers:
                    c = 1.0 / self.attack_mult(q, p)
                    if c < dist.get(p, math.inf):
                        dist[p] = c
        heap = [(c, p) for p, c in dist.items()]
        heapq.heapify(heap)
        while heap:
            c, p = heapq.heappop(heap)
            if c > dist.get(p, math.inf):
                continue
            for n in self.nb(p):
                if n in own:
                    nc = c + 1.0 / self.attack_mult(p, n)
                    if nc < dist.get(n, math.inf):
                        dist[n] = nc
                        heapq.heappush(heap, (nc, n))
        return {p: dist.get(p, math.inf) for p in own}

    def need(self, p, sources, div_width, foes_here=None, foe_width=DIVISION_WIDTH):
        """Divisions province P needs to hold when attacked from SOURCES by FOES_HERE divisions (None: as many as
        the battle takes). With both armies' stats (set_forces): the fewest of ours whose fighting takes no more
        damage per division than it deals, the attack cut by terrain, river and forts; then a reserve on top. At
        most what our battle and its reserve take (more can't fight: the province can't be held by numbers)."""
        mult = max(self.attack_mult(q, p) for q in sources)
        width = self.battle_width(p, len(sources))
        fit = self.s.reserve * width / foe_width
        attackers = fit if foes_here is None else min(fit, foes_here)
        cap = self.s.reserve * width / div_width
        if not self.forces:
            return min(attackers * mult / self.s.defender_edge, cap)
        us, them = self.forces
        wa = min(width, attackers * foe_width)  # attacker width fighting
        if wa <= 0:
            return 0.0

        def hits(attack, defence):  # attacks beyond the defence (or breakthrough) land four times as often
            return 0.1 * min(attack, defence) + 0.4 * max(0.0, attack - defence)
        n = 0.0
        while n < cap:
            n += 0.5
            wd = min(width, n * div_width)
            taken = hits(them["soft_attack"] * wa * mult, us["defense"] * wd) / wd
            dealt = hits(us["soft_attack"] * wd, them["breakthrough"] * wa) / wa
            if taken <= dealt * self.s.defender_edge:
                return min(cap, n * (1 + self.s.rotation))
        return cap

    def set_forces(self, ours, theirs):
        """Average stats per width of a division: {"soft_attack", "defense", "breakthrough"} for each army."""
        self.forces = (ours, theirs) if ours and theirs else None

    def lines(self, ctrl, owner, attackers, div_width=DIVISION_WIDTH, foes=None, foe_width=DIVISION_WIDTH):
        """Candidate lines, from the border inwards (one per distinct set of front provinces): for a price on land
        from cheap to dear, the land worth keeping at that price (a minimum cut: every crossing the attacker could
        attack over costs what holding it needs, every province given up costs its worth x the price)."""
        import networkx as nx
        m, s = self.m, self.s
        own = {p for p in m.provinces if m.is_land(p) and ctrl[p] == self.me}
        owned = [p for p in m.provinces if m.is_land(p) and owner[p] == self.me]
        vps = {p: v for st in m.states.values() for p, v in st.victory_points.items()}
        total_vp = sum(vps.get(p, 0) for p in owned) or 1.0
        state_size = collections.Counter(self.state_of.get(p) for p in owned)
        total_fac = sum(self.factories.get(x, 0) for x in state_size) or 1.0
        self.reach_cost = self.reach(ctrl, attackers, own)

        def fac(p):
            st = self.state_of.get(p)
            return self.factories.get(st, 0) / state_size[st] if owner[p] == self.me and state_size[st] else 0.0

        worth = {p: (vps.get(p, 0) / total_vp if owner[p] == self.me else 0.0) + s.factory_worth * fac(p) / total_fac
                 + s.land_worth / max(1, len(own)) for p in own}
        g = nx.DiGraph()
        for p in own:
            w = self.battle_width(p, 1) / 70.0
            for q in self.nb(p):
                c = s.hold * self.attack_mult(q, p) * w * s.edge_share
                if ctrl[q] in attackers:
                    g.add_edge(p, "T", capacity=g.get_edge_data(p, "T", {}).get("capacity", 0) + c)
                elif q in own:
                    g.add_edge(p, q, capacity=c)
        out, seen = [], set()
        if "T" not in g:
            return out
        # every price on land, and with it lines at least so deep: all land the attacker's reach puts beyond a depth
        # is kept whatever it costs (a line behind the Vistula is no cut at any one price, but the best at its depth)
        finite = sorted(c for c in self.reach_cost.values() if c < math.inf)
        depths = [math.inf] + [finite[int(len(finite) * k / s.depths)] for k in range(s.depths)]
        for depth in depths:
            for i in range(s.prices):
                price = s.price_min * (s.price_max / s.price_min) ** (i / (s.prices - 1))
                for p, v in worth.items():
                    g.add_edge("S", p, capacity=1e9 if self.reach_cost[p] >= depth else price * v)
                _, (side, _) = nx.minimum_cut(g, "S", "T")
                keep = self._main_piece(side - {"S"}, worth)
                if not keep or keep in seen:
                    continue
                seen.add(keep)
                approaches = {}
                for p in keep:
                    src = [q for q in self.nb(p) if q not in keep and (ctrl[q] in attackers or q in own)]
                    if src:
                        approaches[p] = src
                if not approaches:
                    continue
                vp = sum(vps.get(p, 0) for p in keep if owner[p] == self.me) / total_vp
                fk = sum(fac(p) for p in keep) / total_fac
                per = None if foes is None else foes / len(approaches)  # the attacker spread along the line
                need = sum(self.need(p, src, div_width, per, foe_width) for p, src in approaches.items())
                line = Line(depth, set(keep), frozenset(approaches), vp, fk, need, approaches)
                # the line's worst loss: its most valuable front province (a city on the line can fall)
                line.front_vp = max((vps.get(p, 0) for p in approaches if owner[p] == self.me), default=0) / total_vp
                out.append(line)
        # only lines nothing beats on all three: victory points, factories, divisions needed
        out = [l for l in out if not any(o.vp >= l.vp and o.factories >= l.factories and o.need <= l.need
                                         and (o.vp, o.factories, o.need) != (l.vp, l.factories, l.need) for o in out)]
        out.sort(key=lambda l: -len(l.keep))
        return out

    def _main_piece(self, keep, worth):
        """The connected piece of KEEP worth the most: the others are pockets, cut off and lost in time."""
        best, best_w, done = frozenset(), -1.0, set()
        for p in keep:
            if p in done:
                continue
            piece, stack = {p}, [p]
            while stack:
                for n in self.nb(stack.pop()):
                    if n in keep and n not in piece:
                        piece.add(n)
                        stack.append(n)
            done |= piece
            w = sum(worth.get(q, 0) for q in piece)
            if w > best_w:
                best, best_w = frozenset(piece), w
        return best

    def choose(self, lines, divisions):
        """(main line, fallback line inside it or None). The main line keeps the most victory points that our
        DIVISIONS can hold with the buffer kept; if none can be held, the one we come closest to holding among
        those keeping the buffer. The fallback is the next line inward that keeps the surrender limit and needs
        the fewest divisions."""
        if not lines:
            return None, None
        floor = 1 - self.r.surrender_limit
        # a line keeps the buffer even after losing its most valuable front province; failing that, it survives it
        safe = [l for l in lines if l.vp - l.front_vp >= floor + self.s.buffer] or \
            [l for l in lines if l.vp - l.front_vp > floor] or [l for l in lines if l.vp > floor] or lines
        held = [l for l in safe if l.need <= divisions]
        main = max(held, key=lambda l: (l.vp, l.factories)) if held else min(safe, key=lambda l: l.need)
        inner = [l for l in lines if l.keep < main.keep and l.vp > floor and l.need < main.need]
        fallback = max(inner, key=lambda l: (l.vp + l.factories) / max(1.0, l.need)) if inner else None
        return main, fallback
