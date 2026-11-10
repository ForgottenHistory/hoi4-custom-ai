"""Army planning, after how a player runs an offensive (Germany vs Poland, 1939):

- Division roles come from their templates' stats (hoi4ai/templates.py): attack power is organisation x strength
  x soft attack against its speed class (the main infantry, or the fast divisions' average), and x the template's
  attack modifier in the target's terrain.
  So mountaineers are picked first to attack hills and mountains, tanks avoid forests, cities and rivers, and
  weak divisions (security divisions: no support companies) are the ones left holding while the others attack.
- An attack counts only the divisions that fit the battle's combat width (the terrain's, widened for every extra
  province attacked from) and takes its attackers from the provinces around in turn: two divisions from two sides
  fight better than two from one.
- Infantry is split evenly over the whole front (a player's rule): every province facing the enemy gets the same
  number, up to max_stack, the main front and the strongest enemy first for the remainder. Only what is left once
  the front is full stands behind the fast army.
- Attacks are decided by a rough battle estimate (hoi4ai/battle.py: both sides' template stats, organisation,
  strength, supply, terrain, combat width, rivers). All-out: the infantry attacks every enemy province next to it
  it should win (infantry_win), sending what stands around until the chance is high (infantry_goal).
- Fast divisions (armour, motorised: see units.py) form one breakthrough army. Before the war it deploys at the
  start of the best route to the enemy's heart; at war its tanks attack along that route (the spearhead: armour,
  attack, breakthrough) while motorised and the rest pour into the gaps, and it keeps going (exploits),
  always toward the cheapest way on to the main objective: the most valuable province of the enemy's main body
  (its largest connected piece of land; pockets are left to the infantry).
- Once there is empty enemy land, every ready fast division drives into it on its own: each to its own target
  (a province worth having: a victory point, a supply hub, a railway, or just land), the best value for the
  distance through empty land, and keeps that target until it is ours or defended. A worn-out fast division
  rests where it stands instead of walking back to the breach.
- The infantry floods empty enemy land too (a breach, an emptied pocket): free infantry, nearest first, up to a
  few provinces deep; one division stays on every front province still facing defenders, and only part of the
  free infantry goes each step, so a reserve is left for the next breach.
- Among near-equal routes the fast army takes the one whose line to the objective leaves the most enemy
  strength on its weaker side: the thrust that cuts the enemy's army most evenly in two.
- A route's cost counts terrain (plains cheap, forest and hills dear, marsh and mountains very dear) and the
  defenders on it, and rivers crossed. The infantry's local attacks lean toward the nearest victory point
  (a bigger one pulls harder: a route ends where the cost plus OBJECTIVE_BASE / (1 + victory points / 5) is
  lowest). The fast army and the infantry reserves follow the main axis.
- Supply: every province draws on its nearest supply hub by land. An area's limit is learned from its divisions:
  below half supply on average, about divisions x their supply share; fully supplied at the limit, one more each step.
  Points, reserves and leftovers go only where there is room; divisions in an overloaded area (resting tanks
  too) move to the nearest area with room.

Pure logic (no game calls). A Planner keeps state between calls (posts, chosen points, best organisation seen)
so orders don't flap. plan() returns {division id: province} and a report; self.view holds what it saw.
"""
import collections
import heapq
import math

from hoi4ai import battle

# cost of moving and fighting through a province, by terrain (plains = 1)
TERRAIN_COST = {"plains": 1.0, "desert": 1.0, "urban": 1.5, "forest": 2.0, "hills": 2.0, "jungle": 3.0,
                "marsh": 3.0, "mountain": 4.0}
# combat width of a battle: the terrain's, plus the second number per extra direction attacked from (the game's
# common/terrain; the controller loads them, these are the base game's)
TERRAIN_WIDTH = {"plains": (70, 35), "desert": (70, 35), "hills": (70, 35), "urban": (80, 40), "forest": (60, 30),
                 "jungle": (60, 30), "marsh": (50, 25), "mountain": (50, 25)}
DIVISION_WIDTH = 18  # a division whose template we don't know (9 infantry battalions)
RIVER_COST = {0: 0.0, 1: 1.0, 2: 2.0}        # added to a route for crossing a small / large river
FULL_DIVISION = 80.0
OBJECTIVE_BASE = 8.0  # power (organisation x strength) of a fresh 1939 infantry division


def power(d):
    """Fighting power of a division: its share of organisation x strength, FULL_DIVISION when full."""
    return FULL_DIVISION * getattr(d, "share", 1.0) * (d.strength / 100000.0)


class Settings:
    # attacks are decided by the battle estimate (hoi4ai/battle.py): attack when the win chance reaches *_win,
    # and send attackers until it reaches *_goal (or the cap / combat width is reached). All-out: the infantry
    # pushes everywhere it should win
    infantry_win = 0.6
    infantry_goal = 0.85
    infantry_hours = 48        # ... and until the fight should be over within this many hours (a quick win costs
                               # less organisation and keeps the push going)
    infantry_hold = False      # True: one division stays home in a province that faces other defenders
    spear_win = 0.55           # the tanks (spearhead)
    spear_goal = 0.9
    spear_hours = 24
    stop_win = 0.3             # attacks under way are called off below this chance
    # pockets: a piece of the target's land cut off from its main body, this many provinces or fewer. Its
    # defenders get no supply and die when it falls, so the infantry attacks it whatever the odds, with all it
    # can fit in the battle, fresh divisions replacing the worn-out ones; the ring around it stays held
    pocket_max = 12
    pocket_cap = 12            # infantry divisions sent at one pocket province
    # an attack that costs neither side strength or organisation for this many planning steps isn't being
    # fought (the game kept the order but started no battle): it is called off and the divisions leave that
    # province alone for stall_rest steps, so others are sent
    stall_steps = 6
    stall_rest = 3
    infantry_reach = 4         # infantry drives into empty enemy land up to this many provinces away
    infantry_drive_share = 0.5  # share of the free infantry that may start a drive each step
    armor_factor = 1.8         # an armoured division (main unit in the armor group) counts this much more when it
                               # attacks: its armour against defenders with little piercing (1939 infantry)
    attack_min_org = 0.55      # share of a division's best organisation it needs to attack
    fast_min_org = 0.40
    stop_org = 0.30            # an attacker below this share of its best organisation stops attacking
    attack_floor = 10000       # no attack below 10 organisation, whatever the share
    fast_cap = 5               # fast divisions sent at one province (the rest look for other openings)
    infantry_cap = 6           # infantry divisions sent at one province
    width_slack = 1.25         # attackers fill the battle's combat width up to this much (a few in reserve
                               # replace the worn-out); attacking from more provinces widens the battle
    fast_reach = 6.0           # fast army: how fast a victory point's pull fades with route cost
    fast_cutoff = 6.0          # ... and a point pulls nothing beyond this many fast_reach (e^-6)
    fast_objectives = 150      # at most this many points pull the fast army (the most valuable)
    fast_foe_weight = 2.0      # fast army: defenders count this much more on its routes (tanks go around strength)
    target_front_only = True   # plan the front against the target only, not its allies' borders
    max_stack = 6              # divisions per province we plan for (more starve: supply, attrition)
    spill_reach = 8            # divisions left over when every post is full go to a front province this close
    axis_tie = 1.15            # fast army starts within this much of the best route compete on how they split the enemy
    sector_size = 3            # front provinces a force spreads over (its point and best neighbours)
    keep = 8                   # planning steps a chosen point is kept while it stays on the front
    point_drop = 1.5           # a chosen fast point is given up when its route is this much dearer than the best
    exploit_reach = 8          # fast divisions drive this many provinces deep into empty enemy land
    hub_value = 3.0            # a supply hub is worth this many victory points (per hub level)
    rail_value = 0.5           # a railway province, per railway level
    supply_short = 0.5         # a supply area whose divisions average below this share of supply is overloaded
                               # (a player's rule: under 50% is bad, a bit short is fine)
    supply_floor = 2           # a supply area's learned limit never goes below this many divisions


class Planner:
    def __init__(self, m, me, fast_units=(), settings=None, armored_units=(), stats=None):
        self.m, self.me = m, me
        self.stats, self.ref_soft = {}, {}
        self.foe_stats = {}         # (owner tag, template name) -> stats (templates.country), for the estimate
        self.set_stats(stats or {})
        self.widths = dict(TERRAIN_WIDTH)
        self.fast_units = set(fast_units)
        self.armored_units = set(armored_units)
        self.s = settings or Settings()
        self.hold = {}              # division id -> province it holds / stages at
        self.points = {}            # province -> (kind 'fast' | 'support', steps left)
        self.drive = {}             # fast division id -> the empty enemy province it drives to
        self.watch = {}             # attacking division id -> (target, step of last loss, strength, share, {defender id: strength})
        self.cool = {}              # division id -> (province it gave up attacking, step it may again)
        self.tick = 0
        self.view = {}
        self.supply_cap = {}        # supply area (its hub province) -> divisions it can supply (learned)
        self._area = None
        self._land_nb = {p: [n for n in nb if m.is_land(n)] for p, nb in m.neighbours.items() if m.is_land(p)}

    # --- helpers ---------------------------------------------------------------------------------------------
    def area(self, p):
        """P's supply area: its nearest supply hub by land (P itself when no hub is in reach)."""
        if self._area is None:
            self._area = {}
            q = collections.deque()
            for h in self.m.hubs:
                if h in self._land_nb:
                    self._area[h] = h
                    q.append(h)
            while q:
                x = q.popleft()
                for n in self._land_nb[x]:
                    if n not in self._area:
                        self._area[n] = self._area[x]
                        q.append(n)
        return self._area.get(p, p)

    def learn_supply(self, mine):
        """Each supply area's limit, from how well the divisions in it are supplied: short of supply, it is about
        divisions x their supply share; fully supplied at the limit, it grows by one a step."""
        by = collections.defaultdict(list)
        for d in mine:
            by[self.area(d.province)].append(min(1.0, getattr(d, "supply", 100000) / 100000))
        for a, shares in by.items():
            n, share = len(shares), sum(shares) / len(shares)
            if share < self.s.supply_short:
                self.supply_cap[a] = max(self.s.supply_floor, n * share)
            elif a in self.supply_cap and n >= self.supply_cap[a]:
                self.supply_cap[a] += 1
        for a in self.supply_cap:
            if a not in by:
                self.supply_cap[a] += 1  # nobody there to measure: it recovers slowly

    def room(self, aload, p):
        """How many more divisions P's supply area takes, given AALOAD per area (inf when never short)."""
        a = self.area(p)
        return self.supply_cap.get(a, math.inf) - aload.get(a, 0)

    def area_load(self, load):
        """{province: divisions} -> {supply area: divisions}."""
        out = collections.Counter()
        for p, v in load.items():
            if p is not None:
                out[self.area(p)] += v
        return out

    def nb(self, p):
        return self._land_nb.get(p, ())

    def bfs(self, sources, allowed):
        dist = {s: 0 for s in sources}
        q = collections.deque(sources)
        while q:
            p = q.popleft()
            for n in self.nb(p):
                if n not in dist and n in allowed:
                    dist[n] = dist[p] + 1
                    q.append(n)
        return dist

    def waypoint(self, start, goal, passable, k=3):
        """Where to send a division from START to GOAL so it goes around what isn't PASSABLE (the game paths the
        shortest way, through enemy-held land): K steps along the shortest route through PASSABLE (GOAL itself if
        that close), or None if there is no such route."""
        if start == goal:
            return goal
        dist = self.bfs([goal], set(passable) | {start, goal})
        if start not in dist:
            return None
        p = start
        for _ in range(k):
            if p == goal:
                break
            p = min((n for n in self.nb(p) if n in dist and dist[n] < dist[p]), key=dist.get)
        return p

    def set_stats(self, stats):
        """STATS: {template name: {"soft_attack", "maximum_speed", "terrain": {terrain: {"attack": modifier}}}}
        (templates.country). Soft attack counts against its speed class (fast: 6 km/h or more): the most used slow
        template (the main infantry), or the fast divisions' average, so each group keeps the strength its margins
        were set for and only the spread inside it changes."""
        self.stats = stats
        self.ref_soft = {}
        for fast in (False, True):
            group = [t for t in stats.values() if t.get("count") and (t.get("maximum_speed", 4) >= 6) == fast]
            n = sum(t["count"] for t in group)
            if not n:
                self.ref_soft[fast] = 0.0
            elif fast:  # a mix of tanks, motorised, light divisions: their average
                self.ref_soft[fast] = sum(t["count"] * t.get("soft_attack", 0) for t in group) / n
            else:  # the main infantry
                self.ref_soft[fast] = max(group, key=lambda t: t["count"]).get("soft_attack", 0)

    def quality(self, d, target=None):
        """How hard a division hits for its organisation x strength, from its template: soft attack against the
        main template of its speed class (set_stats), times its attack modifier in TARGET's terrain."""
        t = self.stats.get(getattr(d, "template", None))
        ref = self.ref_soft.get(t.get("maximum_speed", 4) >= 6) if t else 0.0
        if not ref:
            return 1.0
        q = min(1.6, max(0.4, t.get("soft_attack", 0) / ref))
        if target is not None and target in self.m.provinces:
            q *= 1.0 + t.get("terrain", {}).get(self.m.provinces[target].terrain, {}).get("attack", 0.0)
        return q

    def member(self, d, foe=False):
        """D as a battle.fight member: its template's stats, organisation, best organisation, strength share."""
        st = self.foe_stats.get((d.owner, d.template)) if foe else self.stats.get(d.template)
        best = (st or {}).get("max_org", 0) * 1000
        best = best or 40000  # template unknown: a typical maximum
        return (st or battle.DEFAULT, d.share * best, best, d.strength / 100000)

    def odds(self, t, attackers, defenders, with_hours=False):
        """Estimated chance ATTACKERS (ours, standing next to T) beat DEFENDERS (in T); with WITH_HOURS also how
        many hours the fight would take."""
        if not defenders or not attackers:
            win = 1.0 if not defenders else 0.0
            return (win, 0) if with_hours else win
        dirs = {d.province for d in attackers}
        river = max(self.m.river(d.province, t) for d in attackers)
        a_sup = sum(min(1.0, d.supply / 100000) for d in attackers) / len(attackers)
        d_sup = sum(min(1.0, d.supply / 100000) for d in defenders) / len(defenders)
        win, hours = battle.fight([self.member(d) for d in attackers], [self.member(d, True) for d in defenders],
                                  self.m.provinces[t].terrain, self.battle_width(t, len(dirs)), len(dirs), river,
                                  a_sup, d_sup)
        return (win, hours) if with_hours else win

    def is_armored(self, d):
        return getattr(d, "unit", None) in self.armored_units

    def width(self, d):
        """A division's combat width (its template's)."""
        t = self.stats.get(getattr(d, "template", None))
        return t["combat_width"] if t and t.get("combat_width") else DIVISION_WIDTH

    def battle_width(self, target, directions):
        """Combat width of a battle for TARGET attacked from DIRECTIONS provinces."""
        base, extra = self.widths.get(self.m.provinces[target].terrain, (70, 35))
        return base + extra * max(0, directions - 1)

    def attack_power(self, d, target=None):
        """A division's power as an attacker (at TARGET): armour counts extra (armor_factor), and its template's
        soft attack and terrain modifier (quality)."""
        return power(d) * self.quality(d, target) * (
            self.s.armor_factor if getattr(d, "unit", None) in self.armored_units else 1.0)

    def is_fast(self, d):
        return getattr(d, "unit", None) in self.fast_units

    def org_share(self, d):
        """Organisation as a share of its maximum."""
        return d.share

    def org_now(self, d):
        """Organisation as the game shows it (x1000): the share of its template's maximum."""
        t = self.stats.get(d.template)
        return d.share * (t["max_org"] * 1000 if t and t.get("max_org") else 40000)

    def can_attack(self, d, min_share):
        return self.org_now(d) >= self.s.attack_floor and self.org_share(d) >= min_share

    def terrain(self, p):
        return TERRAIN_COST.get(self.m.provinces[p].terrain, 2.0)

    def routes(self, sources, allowed, foe_power, foe_weight=1.0, max_cost=math.inf):
        """Cost from each province in ALLOWED to the cheapest of SOURCES ({province: starting cost}) (Dijkstra),
        counting each province's terrain and defenders on the way (the province itself included) and the
        rivers crossed. Provinces beyond MAX_COST are left out (not searched)."""
        def step(p):
            return self.terrain(p) * (1.0 + foe_weight * foe_power.get(p, 0.0) / FULL_DIVISION)
        dist = {g: c0 + step(g) for g, c0 in sources.items() if g in allowed}
        heap = [(c, g) for g, c in dist.items()]
        heapq.heapify(heap)
        while heap:
            c, p = heapq.heappop(heap)
            if c > dist.get(p, math.inf):
                continue
            if c > max_cost:
                break
            for n in self.nb(p):
                if n in allowed:
                    nc = c + step(n) + RIVER_COST[self.m.river(n, p)]
                    if nc < dist.get(n, math.inf):
                        dist[n] = nc
                        heapq.heappush(heap, (nc, n))
        return dist

    def fast_routes(self, objectives, allowed, foe_power):
        """The fast army's cost from each enemy province: a soft minimum over all OBJECTIVES ({province: victory
        points}) of the route cost there, each weighted by its share of the points. Many cheap, lightly held points
        (an open rear) pull as much as one big defended city; defenders count fast_foe_weight times."""
        if len(objectives) > self.s.fast_objectives:  # a big country: its most valuable points only
            objectives = dict(sorted(objectives.items(), key=lambda kv: -kv[1])[:self.s.fast_objectives])
        total = sum(objectives.values())
        if not total:
            return {}
        pull = collections.defaultdict(float)
        for q, v in objectives.items():
            # beyond fast_cutoff x fast_reach a point's pull is negligible (e^-6): don't search there (a big
            # country has hundreds of points; searching each over all its land took seconds)
            for t, c in self.routes({q: 0.0}, allowed, foe_power, self.s.fast_foe_weight,
                                    self.s.fast_cutoff * self.s.fast_reach).items():
                pull[t] += v / total * math.exp(-c / self.s.fast_reach)
        return {t: -self.s.fast_reach * math.log(a) for t, a in pull.items() if a > 0}

    def split(self, start, goal, foe_power, land):
        """Enemy strength on the weaker side of the line from START to GOAL: a thrust along it cuts the enemy's
        army in two, and the more even the halves, the more it cuts off."""
        pv = self.m.provinces
        sx, sy, gx, gy = pv[start].x, pv[start].y, pv[goal].x, pv[goal].y
        left = right = 0.0
        for q, f in foe_power.items():
            if q in land:
                side = (gx - sx) * (pv[q].y - sy) - (gy - sy) * (pv[q].x - sx)
                if side > 0:
                    left += f
                else:
                    right += f
        return min(left, right)

    def place(self, b, n, score, own, load, demand, front=()):
        """Put N divisions at point B: its sector first, then nearby FRONT provinces, then the provinces behind,
        nearest first, at most max_stack per province counting what LOAD already has there. Adds to DEMAND;
        returns the provinces."""
        sec = self.sector(b, score)
        order = list(sec)
        dist = self.bfs(sec, own)
        order += sorted((q for q in dist if q not in sec),
                        key=lambda q: (dist[q], q not in front, score.get(q, math.inf)))
        used = []
        aload = self.area_load(load)
        for q in order:
            if n <= 0:
                break
            take = min(n, self.s.max_stack - load.get(q, 0), max(0, int(min(n, self.room(aload, q)))))
            if take <= 0:
                continue
            demand[q] = demand.get(q, 0) + take
            load[q] = load.get(q, 0) + take
            aload[self.area(q)] += take
            used.append(q)
            n -= take
        return used

    def sector(self, b, score):
        """B and its best-scoring neighbours on the same front (SCORE: lower is better)."""
        nbs = sorted((n for n in self.nb(b) if n in score), key=score.get)
        return [b] + nbs[:self.s.sector_size - 1]

    # --- planning --------------------------------------------------------------------------------------------
    def plan(self, ctrl, divisions, my_tag, enemies, target, at_war, friends=(), zones=None):
        """ctrl: controller country id per province (indexable). enemies: country ids at war with us.
        target: the country id we mean to attack (also before the war). friends: countries whose land our
        front runs along (subjects, faction members). zones: {province: army id}: an army listed there only takes
        posts in its own zone (it holds its own stretch of the front, where its battle plan is)."""
        self.tick += 1
        m, s = self.m, self.s
        hostile = set(enemies) | ({target} if target else set())
        land = self._land_nb
        ours = {self.me} | set(friends)
        own = {p for p in land if ctrl[p] in ours}
        mine = [d for d in divisions if d.owner == my_tag and d.province in land]
        for d in mine:  # see every division's organisation first, so template bests are known this step
            self.org_share(d)
        self.learn_supply(mine)
        foe_power = collections.Counter()
        foes_at = collections.defaultdict(list)
        for d in divisions:
            if d.owner != my_tag and d.province in land and ctrl[d.province] in hostile:
                foe_power[d.province] += power(d)
                foes_at[d.province].append(d)

        facing = {p: [q for q in self.nb(p) if ctrl[q] in hostile] for p in own}
        front = {p for p, f in facing.items() if f}
        main = {p for p in front if any(ctrl[q] == target for q in facing[p])}
        if s.target_front_only and main:
            # other enemies' borders (France joining a war on Poland) are left to the armies the player keeps
            # (controller --leave): spreading the infantry over them pulled it away from the target
            front = main

        enemy_land = {p for p in land if ctrl[p] in hostile}
        # objectives: the target's victory points; its main body: its largest connected piece of land
        vps = {p: v for st in m.states.values() for p, v in st.victory_points.items()
               if p in land and ctrl[p] == target} if target else {}
        target_land = {p for p in enemy_land if ctrl[p] == target}

        def weight(piece):
            return sum(vps.get(q, 0) for q in piece) + len(piece)
        body, seen = set(), set()
        for p in target_land:
            if p not in seen:
                piece = set(self.bfs([p], target_land))
                seen |= piece
                if weight(piece) > weight(body):
                    body = piece
        # what a province is worth taking: victory points, supply hubs, railways (the enemy's supply and movement)
        def value(p):
            return vps.get(p, 0) + s.hub_value * m.hubs.get(p, 0) + s.rail_value * m.rails.get(p, 0)
        worth = {p: value(p) for p in target_land if value(p) > 0}
        start = {p: OBJECTIVE_BASE / (1 + v / 5.0) for p, v in vps.items()}
        route = self.routes(start, enemy_land, foe_power)  # infantry's local attacks: the nearest objective
        # the main axis (fast army, infantry reserves): the most valuable province of the enemy's main body
        goal = max((p for p in vps if p in body), key=vps.get, default=None)
        # the fast army goes for points behind the enemy's line; those at the line are the infantry's
        at_line = {q for p in front for q in facing[p] if ctrl[q] == target}
        depth = self.bfs(at_line, target_land) if at_line else {}
        deep = {p: v for p, v in worth.items() if p in body and depth.get(p, 9) >= 2}
        fast_route = self.fast_routes(deep or {p: v for p, v in worth.items() if p in body}, enemy_land, foe_power)

        def route_from(p, r=route):
            """Best route cost to an objective starting with an attack out of our province P."""
            return min((r[t] + RIVER_COST[m.river(p, t)] for t in facing.get(p, ()) if t in r), default=math.inf)

        # --- territory pieces connected by land (mainland, exclaves) are planned separately ------------------
        comp = {}
        for p in own:
            if p not in comp:
                for q in self.bfs([p], own):
                    comp[q] = p

        for p in list(self.points):
            kind, left = self.points[p]
            if left <= 1 or p not in main:
                del self.points[p]
            else:
                self.points[p] = (kind, left - 1)

        fast = [d for d in mine if self.is_fast(d)]
        line = [d for d in mine if not self.is_fast(d)]
        line_in = collections.Counter(comp[d.province] for d in line if d.province in comp)
        fast_in = collections.Counter(comp[d.province] for d in fast if d.province in comp)
        front_in = collections.defaultdict(set)
        for p in front:
            front_in[comp[p]].add(p)

        pocket = target_land - body

        def needs_holder(p):
            f = facing[p]
            if not f or not all(q in pocket for q in f):
                return True
            return any(foe_power[q] > 0 for q in f)

        line_demand, fast_demand, sectors = {}, {}, {}
        for c, fr in front_in.items():
            fr = {p for p in fr if needs_holder(p)}
            if not fr:
                continue
            score = {p: route_from(p) for p in fr if p in main}
            score = {p: v for p, v in score.items() if v < math.inf}
            fscore = {p: route_from(p, fast_route) for p in fr if p in main}
            fscore = {p: v for p, v in fscore.items() if v < math.inf}
            body_front = set(fscore) or set(score)  # where reserves may spread along the line
            n_line, n_fast = line_in[c], fast_in[c]
            # infantry is split evenly over the whole front: one division a province per round (the main front and
            # where the enemy is strongest first), up to max_stack and only where supply has room. What is left
            # once every front province is full stands behind the fast army
            ranked = sorted(fr, key=lambda p: (p in main, sum(foe_power[q] for q in facing[p])), reverse=True)
            aload = self.area_load(line_demand)
            reserves = n_line
            while reserves:
                took = 0
                for p in ranked:
                    if not reserves:
                        break
                    if line_demand.get(p, 0) >= s.max_stack or self.room(aload, p) < 1:
                        continue
                    line_demand[p] = line_demand.get(p, 0) + 1
                    aload[self.area(p)] += 1
                    reserves -= 1
                    took += 1
                if not took:
                    break
            if not score:
                continue
            # the fast army: one sector, at the start of the best route into the enemy's main body. At war, where
            # we border empty land of it if we do (the breach: tanks go through and on, the infantry follows)
            breach = {p: v for p, v in fscore.items()
                      if any(q in body and foe_power[q] == 0 for q in facing[p])} if at_war else {}
            fcand = breach or fscore
            fpts = [p for p, (k, _) in self.points.items() if k == "fast" and comp.get(p) == c]
            for b in fpts:  # drop a point that is no longer near the best route
                if b not in fcand or fcand[b] > min(fcand.values()) * s.point_drop:
                    del self.points[b]
            fpts = [b for b in fpts if b in self.points]
            if n_fast and not fpts and fcand:
                # near-equal routes: the one that splits the enemy's army most evenly
                low = min(fcand.values())
                near = [p for p, v in fcand.items() if v <= low * s.axis_tie]
                best = max(near, key=lambda p: (self.split(p, goal, foe_power, target_land), -fcand[p]))
                self.points[best] = ("fast", s.keep)
                fpts = [best]
            # the fast army stacks on top of the infantry line: max_stack counts fast divisions only
            fload = {}
            for b in fpts:
                placed = self.place(b, math.ceil(n_fast / len(fpts)), fcand, own, fload, fast_demand, body_front)
                sectors[b] = ("fast", placed)
            # infantry left over when the whole front is full backs the fast army (to follow and widen the breach)
            if reserves:
                load = dict(line_demand)
                pts = fpts or [min(score, key=score.get)]
                per = math.ceil(reserves / len(pts))
                for b in pts:
                    placed = self.place(b, per, fcand if fpts else score, own, load, line_demand, body_front)
                    sectors[b] = (sectors.get(b, ("support", []))[0],
                                  sorted(set(sectors.get(b, ("", []))[1]) | set(placed)))

        self.view = dict(front=front, main=main, goal=goal, foe_power=dict(foe_power), component=comp,
                         route=fast_route, near_route=route, fast_route=fast_route, objectives=vps, body=body,
                         front_route={p: route_from(p, fast_route) for p in main}, sectors=sectors,
                         line_demand=line_demand, fast_demand=fast_demand,
                         fast={d.id for d in fast})

        orders, attacking = {}, set()
        report = collections.Counter()
        self.stops = []
        pockets = set()
        for p in target_land - body:
            if p not in pockets:
                piece = set(self.bfs([p], target_land))
                if len(piece) <= s.pocket_max:
                    pockets |= piece
        if at_war and target in enemies:
            here = collections.defaultdict(list)
            for d in mine:
                # free to attack: standing, or only repositioning on our land (a division forever walking to its
                # post would never attack); one already attacking or driving into enemy land is left to it
                busy = d.moving and d.dest in land and ctrl[d.dest] in hostile
                if not busy and d.province in land and ctrl[d.province] in ours:
                    here[d.province].append(d)

            under_way = collections.defaultdict(list)  # attacks already sent, by target
            for d in mine:
                if d.moving and d.dest in land and ctrl[d.dest] in hostile:
                    under_way[d.dest].append(d)

            # stalled attacks: next to the target, and neither we nor any defender lost anything for stall_steps steps
            # (each defender on its own: fresh ones joining must not hide that the others stopped losing)
            watch = {}
            for t, ds in list(under_way.items()):
                foes = {f.id: f.strength for f in foes_at[t]}
                for d in list(ds):
                    if t not in self.nb(d.province) or not foes_at[t]:
                        continue  # still on the way, or nobody to fight
                    w = self.watch.get(d.id)
                    lost = w is None or w[0] != t or d.strength < w[2] or d.share < w[3] - 0.005 or any(
                        foes.get(f, 0) < st - 200 for f, st in w[4].items())
                    since = self.tick if lost else w[1]
                    watch[d.id] = (t, since, d.strength, d.share, foes)
                    if self.tick - since >= s.stall_steps:
                        ds.remove(d)
                        self.stops.append(d)
                        attacking.add(d.id)
                        self.cool[d.id] = (t, self.tick + s.stall_rest)
                        watch.pop(d.id)
                        report["stalled"] += 1
            self.watch = watch
            self.cool = {k: v for k, v in self.cool.items() if v[1] > self.tick}

            def attack(targets, eligible, min_win, goal, quick, min_org, keep_home, label, cap):
                # the battle estimate decides: attackers are added (the best of each province around in turn, as
                # many directions as possible, within the battle's combat width) until the win chance reaches
                # GOAL and the fight should be over within QUICK hours, or the CAP; the attack goes when it reaches MIN_WIN (a player sends what stands next to
                # the province, and more directions widen the battle)
                for t in targets:
                    ap = lambda d: self.attack_power(d, t)
                    room = cap - len(under_way[t])
                    if room <= 0:
                        continue
                    if under_way[t]:
                        win, hours = self.odds(t, under_way[t], foes_at[t], True)
                        if win >= goal and hours <= quick:
                            continue  # enough on the way
                    pool = []
                    for p in self.nb(t):
                        staying = [d for d in here.get(p, ()) if d.id not in attacking]
                        ready = sorted((d for d in staying if eligible(d) and self.can_attack(d, min_org)
                                        and self.cool.get(d.id, (None,))[0] != t),
                                       key=ap, reverse=True)
                        exposed = keep_home and any(foe_power[q] > 0 for q in facing.get(p, ()) if q != t)
                        pool += ready[:max(0, len(staying) - 1)] if exposed else ready
                    by_src = collections.defaultdict(list)
                    for d in pool:
                        by_src[d.province].append(d)
                    srcs = sorted(by_src, key=lambda p: -ap(by_src[p][0]))
                    order = []
                    for k in range(max((len(v) for v in by_src.values()), default=0)):
                        order += [by_src[p][k] for p in srcs if k < len(by_src[p])]
                    dirs = {d.province for d in under_way[t]}
                    used = sum(self.width(d) for d in under_way[t])
                    chosen, win = [], 0.0
                    for d in order:
                        if len(chosen) >= room:
                            break
                        if used + self.width(d) > self.battle_width(t, len(dirs | {d.province})) * s.width_slack:
                            continue  # no room in the battle from here
                        chosen.append(d)
                        dirs.add(d.province)
                        used += self.width(d)
                        win, hours = self.odds(t, under_way[t] + chosen, foes_at[t], True)
                        if win >= goal and hours <= quick:
                            break
                    if chosen and win >= min_win:
                        under_way[t] += chosen
                        for d in chosen:
                            orders[d.id] = t
                            attacking.add(d.id)
                        report[label] += 1
                        report[label + "_divisions"] += len(chosen)

            # the fast army first: wherever it stands, push along the cheapest way into the enemy's main body
            # the fast army leaves pockets to the infantry: it fights and drives only into the enemy's main body
            # (and other enemies' land), where its speed counts; light tanks and motorised are no assault troops
            def forward(t):
                return t in body or (ctrl[t] in hostile and ctrl[t] != target)
            fast_at = {d.province for d in fast if d.id not in attacking}
            fast_targets = sorted({t for p in fast_at for t in self.nb(p)
                                   if ctrl[t] in hostile and t in fast_route and forward(t)}, key=fast_route.get)
            # the tanks are the spearhead: they take on the defended provinces on the way (armour, attack,
            # breakthrough); motorised and other fast divisions follow and flood the gaps (below). Without tanks
            # the whole fast army attacks
            spear = self.is_armored if any(self.is_armored(d) for d in fast) else self.is_fast
            attack(fast_targets, spear, s.spear_win, s.spear_goal, s.spear_hours, s.fast_min_org, False, "fast_attacks", s.fast_cap)
            # exploitation: a ready fast division not in a fight drives into empty enemy land, each to its own target
            empty = {p for p in enemy_land if foe_power[p] == 0 and forward(p)}
            passable = own | empty
            claimed = {d.dest for d in mine if d.moving and d.dest in empty}
            drivers = [d for d in fast if d.id not in attacking and d.province in passable
                       and not (d.moving and d.dest in land and ctrl[d.dest] in hostile)
                       and self.can_attack(d, s.fast_min_org)]
            for d in sorted(drivers, key=power, reverse=True):
                t = self.drive.get(d.id)
                if t not in empty or t in claimed:
                    dist = {d.province: 0}
                    q = collections.deque([d.province])
                    while q:
                        p = q.popleft()
                        if dist[p] < s.exploit_reach:
                            for n in self.nb(p):
                                if n not in dist and n in passable:
                                    dist[n] = dist[p] + 1
                                    q.append(n)
                    cands = [((1 + value(x)) / (1 + dist[x]), x) for x in dist if x in empty and x not in claimed]
                    t = max(cands)[1] if cands else None
                if t is None:
                    self.drive.pop(d.id, None)
                    continue
                self.drive[d.id] = t
                claimed.add(t)
                orders[d.id] = t
                attacking.add(d.id)
                report["drives"] += 1
            # pockets first: everything that fits, whatever the odds, the weakest defenders first
            in_pocket = sorted({t for p in front for t in facing[p] if t in pockets and foe_power[t] > 0},
                               key=lambda t: foe_power[t])
            attack(in_pocket, lambda d: not self.is_fast(d), 0.0, 2.0, 0, s.attack_min_org, True, "pocket_attacks",
                   s.pocket_cap)
            # infantry: local attacks where clearly stronger; weak, open, on-route provinces first
            cands = {t for p in front for t in facing[p] if ctrl[t] == target and t not in pockets}
            ranked = sorted(cands, key=lambda t: (foe_power[t] + 10) * self.terrain(t) + route.get(t, 99) / 10)
            attack(ranked, lambda d: not self.is_fast(d), s.infantry_win, s.infantry_goal, s.infantry_hours,
                   s.attack_min_org,
                   s.infantry_hold, "attacks", s.infantry_cap)
            # infantry exploitation: once there is empty enemy land (a breach, a pocket emptied), free infantry
            # floods it, nearest first, so the enemy can't close the gap; one division stays on every front province
            # that still faces defenders, and at most a share of the free infantry goes each step (the rest is the
            # reserve for the next breach)
            open_land = {p for p in enemy_land if foe_power[p] == 0}
            if open_land:
                passable_inf = own | open_land
                to_open = self.bfs(list(open_land), passable_inf)  # steps to the nearest empty enemy province
                held = set()
                free = []
                for d in sorted(line, key=self.attack_power):
                    if d.id in attacking or d.province not in own or not self.can_attack(d, s.attack_min_org):
                        continue
                    if d.moving and d.dest in land and ctrl[d.dest] in hostile:
                        continue
                    p = d.province
                    if p in front and any(foe_power[q] > 0 for q in facing[p]) and p not in held:
                        held.add(p)  # the weakest stays and holds; the stronger ones may go
                        continue
                    if to_open.get(p, 99) <= s.infantry_reach:
                        free.append(d)
                free.sort(key=lambda d: to_open[d.province])
                claimed_inf = {d.dest for d in mine if d.moving and d.dest in open_land}
                for d in free[:math.ceil(len(free) * s.infantry_drive_share)]:
                    t = self.drive.get(d.id)
                    if t not in open_land or t in claimed_inf:
                        dist = {d.province: 0}
                        q = collections.deque([d.province])
                        while q:
                            p = q.popleft()
                            if dist[p] < s.infantry_reach:
                                for n in self.nb(p):
                                    if n not in dist and n in passable_inf:
                                        dist[n] = dist[p] + 1
                                        q.append(n)
                        cands = [((1 + value(x)) / (1 + dist[x]) ** 2, x) for x in dist
                                 if x in open_land and x not in claimed_inf]
                        t = max(cands)[1] if cands else None
                    if t is None:
                        self.drive.pop(d.id, None)
                        continue
                    self.drive[d.id] = t
                    claimed_inf.add(t)
                    orders[d.id] = t
                    attacking.add(d.id)
                    report["infantry_drives"] += 1

        # stop attacks: a worn-out attacker, or everyone on a province we no longer outweigh (a player's micro)
        on_target = collections.defaultdict(list)
        for d in mine:
            if d.id not in attacking and d.moving and d.dest in land and ctrl[d.dest] in hostile:
                on_target[d.dest].append(d)
        for t, ds in on_target.items():
            hopeless = foe_power[t] > 0 and t not in pockets and self.odds(t, ds, foes_at[t]) < s.stop_win
            for d in ds:
                if hopeless or self.org_share(d) < s.stop_org:
                    self.stops.append(d)
                    attacking.add(d.id)  # leave it alone this step
                    report["stops"] += 1

        # --- everyone else: fill the posts, nearest first, keeping last step's posts ---------------------------
        unposted = 0
        # at war a worn-out fast division rests where it stands (on our land) instead of walking back to the breach
        resting = {d.id for d in fast if at_war and d.province in own and d.id not in attacking
                   and not self.can_attack(d, s.fast_min_org)}
        # ... unless its supply area is overloaded: then it rests in the nearest area with room
        in_area = collections.Counter(self.area(d.province) for d in mine)
        crowded = {d.id for d in fast if d.id in resting
                   and in_area[self.area(d.province)] > self.supply_cap.get(self.area(d.province), math.inf)}
        resting -= crowded
        report["resting"] = len(resting)
        for group, demand in ((line, line_demand), ([d for d in fast if d.id not in resting], fast_demand)):
            unposted += self._assign(group, demand, own, attacking, ctrl, hostile, orders, zones, front, crowded)
        for did, p in orders.items():
            if did not in attacking:
                self.hold[did] = p

        report.update(divisions=len(mine), fast=len(fast), front=len(front), main_front=len(main),
                      fast_points=sum(1 for k, _ in self.points.values() if k == "fast"),
                      support_points=sum(1 for k, _ in self.points.values() if k == "support"),
                      unposted=unposted, enemy_power=round(sum(foe_power.values()), 1),
                      our_power=round(sum(power(d) for d in mine), 1), goal=goal or 0,
                      short_of_supply=sum(1 for d in mine
                                          if getattr(d, "supply", 100000) < s.supply_short * 100000),
                      max_stack=max(collections.Counter(d.province for d in mine).values(), default=0),
                      overloaded=sum(1 for a, c in self.supply_cap.items() if in_area[a] > c),
                      over_supply=round(sum(max(0, in_area[a] - c) for a, c in self.supply_cap.items())))
        return orders, report

    def _assign(self, group, demand, own, attacking, ctrl, hostile, orders, zones=None, front=(), rear=()):
        """REAR: divisions that only need a supplied place to rest (the nearest area with room)."""
        zoned = set(zones.values()) if zones else set()

        def allowed(d, p):
            return d.army not in zoned or zones.get(p) in (None, d.army)

        left = dict(demand)
        free = []
        for d in group:
            if d.id in attacking:
                continue
            if d.moving and d.dest in self._land_nb and ctrl[d.dest] in hostile:
                continue  # an attack under way: let it finish
            if d.id in rear:
                free.append(d)
                continue
            p = self.hold.get(d.id)
            if p in left and left[p] > 0 and allowed(d, p):
                left[p] -= 1
                orders.setdefault(d.id, p)
            else:
                free.append(d)
        if not free:
            return 0
        posts = [p for p in demand]
        # more divisions than posts: the rest spread along the whole front (next to the enemy, where they pin it
        # and can attack), least crowded first, instead of piling up on the nearest post
        spill = [p for p in front if p in own and p not in demand]
        reach = own | set(posts)
        dist_cache, pairs = {}, []
        for d in free:
            if d.province not in own:
                continue
            if d.province not in dist_cache:
                dist_cache[d.province] = self.bfs([d.province], reach)
            for p in posts:
                if left[p] > 0 and p in dist_cache[d.province] and allowed(d, p):
                    pairs.append((dist_cache[d.province][p], d.id, p))
        pairs.sort()
        done = set()
        for _, did, p in pairs:
            if did in done or left[p] <= 0 or did in rear:
                continue
            left[p] -= 1
            done.add(did)
            orders[did] = p
        load = collections.Counter(orders.get(d.id) for d in group)
        aload = self.area_load(collections.Counter(orders.values()))
        for d in free:  # all posts full or out of reach: the least crowded front province or post (its own zone's)
            if d.id in done or d.province not in dist_cache:
                continue
            dc = dist_cache[d.province]
            if d.id in rear or self.room(aload, d.province) < 0:
                # resting, or left over where supply is overloaded: the nearest province of an area with room,
                # away from the front if possible
                cands = [(q in front, dc[q], q) for q in dc if q in own and self.room(aload, q) >= 1]
                if cands:
                    p = min(cands)[-1]
                    orders[d.id] = p
                    aload[self.area(p)] += 1
                    done.add(d.id)
                continue
            # nearby first; each division already there counts as two steps further (they spread locally). An
            # army whose zone is full (the front moved on: its stretch may have one front province left) spills
            # into nearby front provinces outside it rather than piling up on one
            cands = [p for p in posts + [q for q in spill if dc.get(q, 99) <= self.s.spill_reach] if p in dc]
            cands = [p for p in cands if self.room(aload, p) >= 1] or cands
            key = lambda p: (load[p] // self.s.max_stack, dc[p] + 2 * load[p], p)
            near = [key(p) for p in cands if allowed(d, p)]
            if not near or min(near)[0] > 0:
                near += [key(p) for p in cands if not allowed(d, p)]
            if not near and d.army in zoned:  # no post in its zone: stand in the zone's front provinces
                near = [(load[p] // self.s.max_stack, 0, p) for p, a in zones.items() if a == d.army and p in own
                        and any(ctrl[q] in hostile for q in self.nb(p))]
            if near:
                p = min(near)[-1]
                orders[d.id] = p
                load[p] += 1
                aload[self.area(p)] += 1
                done.add(d.id)
        return len(free) - len(done)
