"""Army planning: hold the border, concentrate reserves at a few breakthrough points, attack where it pays.

Pure logic (no game calls): plan() takes the map, who controls each province, and every division, and returns
the province each of our divisions should go to. A Planner keeps state between calls (who holds where, the
chosen breakthrough points, each division's highest organisation seen), so orders don't flap.

The idea, from comparing a player's invasion of Poland (15 days) with the game AI's (36 days): the player held
the line thinly, put the mass of divisions on a few chosen provinces, and attacked there; the AI spread
divisions along whole fronts and kept reshuffling them.
"""
import collections
import math

TERRAIN_COST = {"plains": 1.0, "desert": 1.0, "forest": 1.3, "jungle": 1.5, "hills": 1.4, "marsh": 1.6,
                "urban": 1.6, "mountain": 2.0}


def power(d):
    """Fighting power of a division: organisation x strength (both as the game shows them)."""
    return (d.org / 1000.0) * (d.strength / 100000.0)


class Settings:
    attack_margin = 1.6        # attack when our power >= margin x enemy power x terrain cost
    attack_min_org = 0.55      # share of a division's best organisation it needs to attack
    retreat_org = 0.20         # attackers below this share are pulled back
    breakthrough_every = 6     # one breakthrough point per this many reserve divisions
    breakthrough_keep = 8      # ticks a breakthrough point is kept while it stays on the front
    max_breakthroughs = 6


class Planner:
    def __init__(self, m, me, settings=None):
        self.m, self.me = m, me
        self.s = settings or Settings()
        self.best_org = {}          # division id -> highest org seen
        self.hold = {}              # division id -> province it holds / stages at
        self.breakthroughs = {}     # province -> ticks left
        self.tick = 0
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

    def org_share(self, d):
        best = max(self.best_org.get(d.id, 0), d.org)
        self.best_org[d.id] = best
        return d.org / best if best else 0.0

    # --- planning --------------------------------------------------------------------------------------------
    def plan(self, ctrl, divisions, my_tag, enemies, target, at_war, friends=()):
        """ctrl: controller country id per province (indexable). enemies: country ids at war with us.
        target: the country id we mean to attack (also before the war). friends: countries whose land our
        divisions may stand on and attack from (subjects, allies, military access). Returns
        (orders {div id: province}, report dict)."""
        self.tick += 1
        m, s = self.m, self.s
        hostile = set(enemies) | ({target} if target else set())
        land = self._land_nb
        ours = {self.me} | set(friends)
        own = {p for p in land if ctrl[p] in ours}
        mine = [d for d in divisions if d.owner == my_tag and d.province in land]
        foe_power = collections.Counter()
        for d in divisions:
            if d.owner != my_tag and d.province in land and ctrl[d.province] in hostile:
                foe_power[d.province] += power(d)
        # count only enemy divisions standing in hostile-controlled provinces (others are elsewhere / allies')

        facing = {p: [q for q in self.nb(p) if ctrl[q] in hostile] for p in own}
        front = {p for p, f in facing.items() if f}
        main = {p for p in front if any(ctrl[q] == target for q in facing[p])}

        # the enemy's most valuable province (its capital in practice) gives the direction of attack
        goal = None
        if target:
            vps = [(v, p) for st in m.states.values() for p, v in st.victory_points.items()
                   if p in land and ctrl[p] == target]
            if vps:
                goal = max(vps)[1]
        to_goal = self.bfs([goal], set(land)) if goal else {}

        def value(t):
            st = m.states.get(m.provinces[t].state)
            vp = st.victory_points.get(t, 0) if st else 0
            return 1.0 + vp / 5.0 + 3.0 / (1 + to_goal.get(t, 50))

        def cost(t):
            return TERRAIN_COST.get(m.provinces[t].terrain, 1.3)

        # --- breakthrough points: front provinces facing weak, valuable enemy provinces ---------------------
        for p in list(self.breakthroughs):
            self.breakthroughs[p] -= 1
            if self.breakthroughs[p] <= 0 or p not in main:
                del self.breakthroughs[p]
        # each piece of our territory connected by land (e.g. the mainland, an exclave) is planned on its own:
        # its divisions hold its front and mass at its own breakthrough points
        comp = {}
        for p in own:
            if p not in comp:
                for q in self.bfs([p], own):
                    comp[q] = p
        divs_in = collections.Counter(comp[d.province] for d in mine if d.province in comp)
        front_in = collections.defaultdict(set)
        for p in front:
            front_in[comp[p]].add(p)
        demand = {}
        for c, fr in front_in.items():
            n = divs_in[c]
            mainc = [p for p in fr if p in main]
            if n < len(fr):  # not enough: hold where the enemy is strongest, the main front first
                ranked = sorted(fr, key=lambda p: (p in main, sum(foe_power[q] for q in facing[p])), reverse=True)
                demand.update({p: 1 for p in ranked[:n]})
                continue
            demand.update({p: 1 for p in fr})
            reserves = n - len(fr)
            mine_bt = [b for b in self.breakthroughs if comp.get(b) == c]
            want = min(s.max_breakthroughs, max(1 if mainc else 0, reserves // s.breakthrough_every))
            if len(mine_bt) < want:
                score = {p: sum(value(t) / ((foe_power[t] + 0.5) * cost(t)) for t in facing[p] if ctrl[t] == target)
                         for p in mainc if p not in self.breakthroughs}
                for p in sorted(score, key=score.get, reverse=True):
                    if len(mine_bt) >= want:
                        break
                    if all(p not in self.nb(b) for b in mine_bt):  # spread them out a little
                        self.breakthroughs[p] = s.breakthrough_keep
                        mine_bt.append(p)
            if mine_bt and reserves:
                per = math.ceil(reserves / len(mine_bt))
                for p in mine_bt:
                    demand[p] += per

        # --- attacks (at war with the target): from where our divisions stand now -----------------------------
        orders, attacking = {}, set()
        report = collections.Counter()
        if at_war and target in enemies:
            here = collections.defaultdict(list)
            for d in mine:
                if not d.moving and d.province in own:
                    here[d.province].append(d)
            cands = {t for p in main for t in facing[p] if ctrl[t] == target}
            ranked = sorted(cands, key=lambda t: value(t) / (foe_power[t] * cost(t) + 0.2), reverse=True)
            for t in ranked:
                need = foe_power[t] * cost(t) * s.attack_margin
                pool = []
                for p in self.nb(t):
                    if p not in own:
                        continue
                    staying = [d for d in here[p] if d.id not in attacking]
                    ready = sorted((d for d in staying if self.org_share(d) >= s.attack_min_org),
                                   key=power, reverse=True)
                    # keep one division home if that province also faces enemy divisions elsewhere
                    exposed = any(foe_power[q] > 0 for q in facing.get(p, ()) if q != t)
                    pool += ready[:max(0, len(staying) - 1)] if exposed else ready
                pool.sort(key=power, reverse=True)
                chosen, total = [], 0.0
                for d in pool:
                    if total > need and chosen:
                        break
                    chosen.append(d)
                    total += power(d)
                if chosen and total > need:
                    for d in chosen:
                        orders[d.id] = t
                        attacking.add(d.id)
                    report["attacks"] += 1
                    report["attackers"] += len(chosen)

        # --- pull back worn-out attackers ------------------------------------------------------------------
        for d in mine:
            if d.moving and d.dest in land and ctrl[d.dest] in hostile and self.org_share(d) < s.retreat_org:
                orders[d.id] = d.province
                attacking.add(d.id)
                report["retreats"] += 1

        # --- everyone else: fill the demand, nearest first, keeping last tick's posts -------------------------
        left = dict(demand)
        free = []
        for d in mine:
            if d.id in attacking:
                continue
            if d.moving and d.dest in land and ctrl[d.dest] in hostile:
                continue  # an attack under way: let it finish
            p = self.hold.get(d.id)
            if p in left and left[p] > 0:
                left[p] -= 1
                orders.setdefault(d.id, p)
            else:
                free.append(d)
        if free:
            open_posts = {p for p, n in left.items() if n > 0}
            reach = own | open_posts
            dist_cache = {}
            pairs = []
            for d in free:
                src = d.province if d.province in own else None
                if src is None:
                    continue
                if src not in dist_cache:
                    dist_cache[src] = self.bfs([src], reach)
                for p in open_posts:
                    if p in dist_cache[src]:
                        pairs.append((dist_cache[src][p], d.id, p))
            pairs.sort()
            byid = {d.id: d for d in free}
            done = set()
            for dist, did, p in pairs:
                if did in done or left[p] <= 0:
                    continue
                left[p] -= 1
                done.add(did)
                orders[did] = p
                self.hold[did] = p
            for d in free:  # posts full or out of reach: go to the nearest breakthrough point we can reach
                if d.id in done or d.province not in dist_cache:
                    continue
                dc = dist_cache[d.province]
                near = [(dc[b], b) for b in self.breakthroughs if b in dc]
                if near:
                    orders[d.id] = min(near)[1]
                    done.add(d.id)
            report["unposted"] = len(byid) - len(done)
        for did, p in orders.items():
            if did not in attacking:
                self.hold[did] = p

        report.update(divisions=len(mine), front=len(front), main_front=len(main),
                      breakthroughs=len(self.breakthroughs), enemy_power=round(sum(foe_power.values()), 1),
                      our_power=round(sum(power(d) for d in mine), 1))
        report["goal"] = goal or 0
        return orders, report
