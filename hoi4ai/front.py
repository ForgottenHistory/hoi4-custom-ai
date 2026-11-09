"""Army planning, after how a player runs an offensive (Germany vs Poland, 1939):

- Infantry holds the line: one division on every province facing the enemy. Half the rest stands behind the
  fast army to follow and widen its breach, the other half at a few support points along good routes; reserves
  fill nearby front provinces before going deep. Infantry attacks locally where it clearly outweighs the
  defenders.
- Fast divisions (armour, motorised: see units.py) form one breakthrough army. Before the war it deploys at the
  start of the best route to the enemy's heart; at war it attacks along that route and keeps going (exploits),
  always toward the cheapest way on to the main objective: the most valuable province of the enemy's main body
  (its largest connected piece of land; pockets are left to the infantry).
- Among near-equal routes the fast army takes the one whose line to the objective leaves the most enemy
  strength on its weaker side: the thrust that cuts the enemy's army most evenly in two.
- A route's cost counts terrain (plains cheap, forest and hills dear, marsh and mountains very dear) and the
  defenders on it, and rivers crossed. The infantry's local attacks lean toward the nearest victory point
  (a bigger one pulls harder: a route ends where the cost plus OBJECTIVE_BASE / (1 + victory points / 5) is
  lowest). The fast army and the infantry reserves follow the main axis.

Pure logic (no game calls). A Planner keeps state between calls (posts, chosen points, best organisation seen)
so orders don't flap. plan() returns {division id: province} and a report; self.view holds what it saw.
"""
import collections
import heapq
import math

# cost of moving and fighting through a province, by terrain (plains = 1)
TERRAIN_COST = {"plains": 1.0, "desert": 1.0, "urban": 1.5, "forest": 2.0, "hills": 2.0, "jungle": 3.0,
                "marsh": 3.0, "mountain": 4.0}
RIVER_COST = {0: 0.0, 1: 1.0, 2: 2.0}        # added to a route for crossing a small / large river
RIVER_ATTACK = {0: 1.0, 1: 1.25, 2: 1.5}     # extra strength wanted to attack across one
FULL_DIVISION = 80.0
OBJECTIVE_BASE = 8.0  # power (organisation x strength) of a fresh 1939 infantry division


def power(d):
    """Fighting power of a division: organisation x strength (both as the game shows them)."""
    return (d.org / 1000.0) * (d.strength / 100000.0)


class Settings:
    attack_margin = 1.6        # infantry attacks when its power >= margin x enemy power x terrain cost
    fast_margin = 1.1          # the same for the fast army
    attack_min_org = 0.55      # share of a division's best organisation it needs to attack
    fast_min_org = 0.40
    stop_org = 0.30            # an attacker below this share of its best organisation stops attacking
    attack_floor = 10000       # no attack below 10 organisation, whatever the share
    fast_cap = 5               # fast divisions sent at one province (the rest look for other openings)
    infantry_cap = 6           # infantry divisions sent at one province
    fast_reach = 6.0           # fast army: how fast a victory point's pull fades with route cost
    fast_foe_weight = 2.0      # fast army: defenders count this much more on its routes (tanks go around strength)
    support_every = 10         # one infantry support point per this many reserve infantry divisions
    max_support = 4
    support_route = 1.2        # support points only on routes at most this much dearer than the best
    max_stack = 6              # divisions per province we plan for (more starve: supply, attrition)
    axis_tie = 1.15            # fast army starts within this much of the best route compete on how they split the enemy
    sector_size = 3            # front provinces a force spreads over (its point and best neighbours)
    keep = 8                   # planning steps a chosen point is kept while it stays on the front


class Planner:
    def __init__(self, m, me, fast_units=(), settings=None):
        self.m, self.me = m, me
        self.fast_units = set(fast_units)
        self.s = settings or Settings()
        self.best_org = {}          # division id -> highest org seen
        self.template_org = {}      # template name -> highest org seen on any division of it
        self.hold = {}              # division id -> province it holds / stages at
        self.points = {}            # province -> (kind 'fast' | 'support', steps left)
        self.tick = 0
        self.view = {}
        self._land_nb = {p: [n for n in nb if m.is_land(n)] for p, nb in m.neighbours.items() if m.is_land(p)}

    # --- helpers ---------------------------------------------------------------------------------------------
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

    def is_fast(self, d):
        return getattr(d, "unit", None) in self.fast_units

    def org_share(self, d):
        """Organisation as a share of the best seen, for the division or any division of its template (so a
        division first seen worn out doesn't count as fresh)."""
        self.best_org[d.id] = max(self.best_org.get(d.id, 0), d.org)
        self.template_org[d.template] = max(self.template_org.get(d.template, 0), d.org)
        best = max(self.best_org[d.id], self.template_org[d.template])
        return d.org / best if best else 0.0

    def can_attack(self, d, min_share):
        return d.org >= self.s.attack_floor and self.org_share(d) >= min_share

    def terrain(self, p):
        return TERRAIN_COST.get(self.m.provinces[p].terrain, 2.0)

    def routes(self, sources, allowed, foe_power, foe_weight=1.0):
        """Cost from each province in ALLOWED to the cheapest of SOURCES ({province: starting cost}) (Dijkstra),
        counting each province's terrain and defenders on the way (the province itself included) and the
        rivers crossed."""
        def step(p):
            return self.terrain(p) * (1.0 + foe_weight * foe_power.get(p, 0.0) / FULL_DIVISION)
        dist = {g: c0 + step(g) for g, c0 in sources.items() if g in allowed}
        heap = [(c, g) for g, c in dist.items()]
        heapq.heapify(heap)
        while heap:
            c, p = heapq.heappop(heap)
            if c > dist.get(p, math.inf):
                continue
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
        total = sum(objectives.values())
        if not total:
            return {}
        pull = collections.defaultdict(float)
        for q, v in objectives.items():
            for t, c in self.routes({q: 0.0}, allowed, foe_power, self.s.fast_foe_weight).items():
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
        for q in order:
            if n <= 0:
                break
            take = min(n, self.s.max_stack - load.get(q, 0))
            if take <= 0:
                continue
            demand[q] = demand.get(q, 0) + take
            load[q] = load.get(q, 0) + take
            used.append(q)
            n -= take
        return used

    def sector(self, b, score):
        """B and its best-scoring neighbours on the same front (SCORE: lower is better)."""
        nbs = sorted((n for n in self.nb(b) if n in score), key=score.get)
        return [b] + nbs[:self.s.sector_size - 1]

    # --- planning --------------------------------------------------------------------------------------------
    def plan(self, ctrl, divisions, my_tag, enemies, target, at_war, friends=()):
        """ctrl: controller country id per province (indexable). enemies: country ids at war with us.
        target: the country id we mean to attack (also before the war). friends: countries whose land our
        front runs along (subjects, faction members)."""
        self.tick += 1
        m, s = self.m, self.s
        hostile = set(enemies) | ({target} if target else set())
        land = self._land_nb
        ours = {self.me} | set(friends)
        own = {p for p in land if ctrl[p] in ours}
        mine = [d for d in divisions if d.owner == my_tag and d.province in land]
        for d in mine:  # see every division's organisation first, so template bests are known this step
            self.org_share(d)
        foe_power = collections.Counter()
        for d in divisions:
            if d.owner != my_tag and d.province in land and ctrl[d.province] in hostile:
                foe_power[d.province] += power(d)

        facing = {p: [q for q in self.nb(p) if ctrl[q] in hostile] for p in own}
        front = {p for p, f in facing.items() if f}
        main = {p for p in front if any(ctrl[q] == target for q in facing[p])}

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
        start = {p: OBJECTIVE_BASE / (1 + v / 5.0) for p, v in vps.items()}
        route = self.routes(start, enemy_land, foe_power)  # infantry's local attacks: the nearest objective
        # the main axis (fast army, infantry reserves): the most valuable province of the enemy's main body
        goal = max((p for p in vps if p in body), key=vps.get, default=None)
        # the fast army goes for points behind the enemy's line; those at the line are the infantry's
        at_line = {q for p in front for q in facing[p] if ctrl[q] == target}
        depth = self.bfs(at_line, target_land) if at_line else {}
        deep = {p: v for p, v in vps.items() if p in body and depth.get(p, 9) >= 2}
        fast_route = self.fast_routes(deep or {p: v for p, v in vps.items() if p in body}, enemy_land, foe_power)

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
            # infantry holds the line; if short, where the enemy is strongest, the main front first
            if n_line < len(fr):
                ranked = sorted(fr, key=lambda p: (p in main, sum(foe_power[q] for q in facing[p])), reverse=True)
                line_demand.update({p: 1 for p in ranked[:n_line]})
                reserves = 0
            else:
                line_demand.update({p: 1 for p in fr})
                reserves = n_line - len(fr)
            if not score:
                continue
            # the fast army: one sector, at the start of the best route into the enemy's main body. At war, where
            # we border empty land of it if we do (the breach: tanks go through and on, the infantry follows)
            breach = {p: v for p, v in fscore.items()
                      if any(q in body and foe_power[q] == 0 for q in facing[p])} if at_war else {}
            fcand = breach or fscore
            fpts = [p for p, (k, _) in self.points.items() if k == "fast" and comp.get(p) == c]
            for b in fpts:  # drop a point that is no longer near the best route
                if b not in fcand or fcand[b] > min(fcand.values()) * s.support_route:
                    del self.points[b]
            fpts = [b for b in fpts if b in self.points]
            if n_fast and not fpts and fcand:
                # near-equal routes: the one that splits the enemy's army most evenly
                low = min(fcand.values())
                near = [p for p, v in fcand.items() if v <= low * s.axis_tie]
                best = max(near, key=lambda p: (self.split(p, goal, foe_power, target_land), -fcand[p]))
                self.points[best] = ("fast", s.keep)
                fpts = [best]
            used = set()
            load = dict(line_demand)  # holders already stand there
            for b in fpts:
                placed = self.place(b, math.ceil(n_fast / len(fpts)), fcand, own, load, fast_demand, body_front)
                sectors[b] = ("fast", placed)
                used.update(self.sector(b, fcand))
            # infantry support points: along the next best routes of the main axis, apart from each other and
            # from the fast sector
            score = fscore or score
            spts = [p for p, (k, _) in self.points.items() if k == "support" and comp.get(p) == c]
            for b in spts:
                used.update(self.sector(b, score))
            want = min(s.max_support, reserves // s.support_every)
            best_route = min(score.values())
            for p in sorted(score, key=score.get):
                if score[p] > best_route * s.support_route:
                    break
                if len(spts) >= want:
                    break
                sec = self.sector(p, score)
                if p in self.points or any(q in used or any(n in used for n in self.nb(q)) for q in sec):
                    continue
                self.points[p] = ("support", s.keep)
                spts.append(p)
                used.update(sec)
            # half the reserve infantry backs the fast army (its shoulders: to follow and widen the breach), the
            # rest goes to the support points (or also to the fast army if there are none)
            behind = reserves if not spts else (reserves // 2 if fpts else 0)
            if fpts and behind:
                per = math.ceil(behind / len(fpts))
                for b in fpts:
                    placed = self.place(b, per, fcand, own, load, line_demand, body_front)
                    sectors[b] = ("fast", sorted(set(sectors[b][1]) | set(placed)))
            if spts and reserves - behind:
                per = math.ceil((reserves - behind) / len(spts))
                for b in spts:
                    placed = self.place(b, per, score, own, load, line_demand, body_front)
                    sectors.setdefault(b, ("support", placed))

        self.view = dict(front=front, main=main, goal=goal, foe_power=dict(foe_power), component=comp,
                         route=fast_route, near_route=route, fast_route=fast_route, objectives=vps, body=body,
                         front_route={p: route_from(p, fast_route) for p in main}, sectors=sectors,
                         line_demand=line_demand, fast_demand=fast_demand,
                         fast={d.id for d in fast})

        orders, attacking = {}, set()
        report = collections.Counter()
        if at_war and target in enemies:
            here = collections.defaultdict(list)
            for d in mine:
                if not d.moving and d.province in land and ctrl[d.province] in ours:
                    here[d.province].append(d)

            under_way = collections.defaultdict(list)  # attacks already sent, by target
            for d in mine:
                if d.moving and d.dest in land and ctrl[d.dest] in hostile:
                    under_way[d.dest].append(d)

            def attack(targets, eligible, margin, min_org, keep_home, label, cap):
                for t in targets:
                    already = sum(power(d) for d in under_way[t])
                    room = cap - len(under_way[t])
                    if room <= 0:
                        continue
                    pool = []
                    for p in self.nb(t):
                        staying = [d for d in here.get(p, ()) if d.id not in attacking]
                        ready = sorted((d for d in staying if eligible(d) and self.can_attack(d, min_org)),
                                       key=power, reverse=True)
                        exposed = keep_home and any(foe_power[q] > 0 for q in facing.get(p, ()) if q != t)
                        pool += ready[:max(0, len(staying) - 1)] if exposed else ready
                    pool.sort(key=power, reverse=True)
                    # attacked across the worst river among the provinces we attack from
                    rv = max((m.river(d.province, t) for d in pool), default=0)
                    need = foe_power[t] * self.terrain(t) * margin * RIVER_ATTACK[rv]
                    if under_way[t] and already > need:
                        continue  # enough on the way
                    chosen, total = [], already
                    for d in pool:
                        if (total > need and chosen) or len(chosen) >= room:
                            break
                        chosen.append(d)
                        total += power(d)
                    if chosen and total > need:
                        under_way[t] += chosen
                        for d in chosen:
                            orders[d.id] = t
                            attacking.add(d.id)
                        report[label] += 1
                        report[label + "_divisions"] += len(chosen)

            # the fast army first: wherever it stands, push along the cheapest way into the enemy's main body
            fast_at = {d.province for d in fast if d.id not in attacking}
            fast_targets = sorted({t for p in fast_at for t in self.nb(p) if ctrl[t] in hostile and t in fast_route},
                                  key=fast_route.get)
            attack(fast_targets, self.is_fast, s.fast_margin, s.fast_min_org, False, "fast_attacks", s.fast_cap)
            # infantry: local attacks where clearly stronger; weak, open, on-route provinces first
            cands = {t for p in front for t in facing[p] if ctrl[t] == target}
            ranked = sorted(cands, key=lambda t: (foe_power[t] + 10) * self.terrain(t) + route.get(t, 99) / 10)
            attack(ranked, lambda d: not self.is_fast(d), s.attack_margin, s.attack_min_org, True, "attacks",
                   s.infantry_cap)

        # stop attacks: a worn-out attacker, or everyone on a province we no longer outweigh (a player's micro)
        self.stops = []
        on_target = collections.defaultdict(list)
        for d in mine:
            if d.id not in attacking and d.moving and d.dest in land and ctrl[d.dest] in hostile:
                on_target[d.dest].append(d)
        for t, ds in on_target.items():
            rv = max((m.river(d.province, t) for d in ds), default=0)
            hopeless = foe_power[t] > 0 and sum(power(d) for d in ds) < foe_power[t] * self.terrain(t) * RIVER_ATTACK[rv]
            for d in ds:
                if hopeless or self.org_share(d) < s.stop_org:
                    self.stops.append(d)
                    attacking.add(d.id)  # leave it alone this step
                    report["stops"] += 1

        # --- everyone else: fill the posts, nearest first, keeping last step's posts ---------------------------
        unposted = 0
        for group, demand in ((line, line_demand), (fast, fast_demand)):
            unposted += self._assign(group, demand, own, attacking, ctrl, hostile, orders)
        for did, p in orders.items():
            if did not in attacking:
                self.hold[did] = p

        report.update(divisions=len(mine), fast=len(fast), front=len(front), main_front=len(main),
                      fast_points=sum(1 for k, _ in self.points.values() if k == "fast"),
                      support_points=sum(1 for k, _ in self.points.values() if k == "support"),
                      unposted=unposted, enemy_power=round(sum(foe_power.values()), 1),
                      our_power=round(sum(power(d) for d in mine), 1), goal=goal or 0,
                      short_of_supply=sum(1 for d in mine if getattr(d, "supply", 100000) < 95000),
                      max_stack=max(collections.Counter(d.province for d in mine).values(), default=0))
        return orders, report

    def _assign(self, group, demand, own, attacking, ctrl, hostile, orders):
        left = dict(demand)
        free = []
        for d in group:
            if d.id in attacking:
                continue
            if d.moving and d.dest in self._land_nb and ctrl[d.dest] in hostile:
                continue  # an attack under way: let it finish
            p = self.hold.get(d.id)
            if p in left and left[p] > 0:
                left[p] -= 1
                orders.setdefault(d.id, p)
            else:
                free.append(d)
        if not free:
            return 0
        posts = [p for p in demand]
        reach = own | set(posts)
        dist_cache, pairs = {}, []
        for d in free:
            if d.province not in own:
                continue
            if d.province not in dist_cache:
                dist_cache[d.province] = self.bfs([d.province], reach)
            for p in posts:
                if left[p] > 0 and p in dist_cache[d.province]:
                    pairs.append((dist_cache[d.province][p], d.id, p))
        pairs.sort()
        done = set()
        for _, did, p in pairs:
            if did in done or left[p] <= 0:
                continue
            left[p] -= 1
            done.add(did)
            orders[did] = p
        for d in free:  # all posts full or out of reach: join the nearest post anyway
            if d.id in done or d.province not in dist_cache:
                continue
            dc = dist_cache[d.province]
            near = [(dc[p], p) for p in posts if p in dc]
            if near:
                orders[d.id] = min(near)[1]
                done.add(d.id)
        return len(free) - len(done)
