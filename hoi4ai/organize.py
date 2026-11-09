"""The army structure, as a player sets it up: one fast army, a few infantry armies, one field marshal.

For the main offensive (Germany vs Poland: one field marshal over five generals):
  - every fast division (armour, motorised) in one army under the general with the best attack;
  - the infantry nearest the target's border in up to `infantry_armies` armies of at most `army_size`, split
    along the front (north to south), under the next best generals;
  - these armies (at most `group_size`) in one army group under the field marshal with the best attack.
Divisions elsewhere (an exclave like East Prussia, the far side of the country) get armies of their own,
outside the group, under the remaining generals.

plan() is pure: it returns the structure; tools/controller.py builds it with bridge.army.
"""
import collections

ARMY_SIZE = 24
GROUP_SIZE = 5


class Army:
    def __init__(self, role, divisions, leader=None):
        self.role, self.divisions, self.leader = role, divisions, leader

    def __repr__(self):
        return "Army(%s, %d divisions, %s)" % (self.role, len(self.divisions), self.leader)


def _chunks(items, size):
    n = max(1, -(-len(items) // size))
    per = -(-len(items) // n)
    return [items[i:i + per] for i in range(0, len(items), per)]


def plan(m, ctrl, divisions, my_tag, me, target, fast_units, leaders, friends=(),
         infantry_armies=4, army_size=ARMY_SIZE, group_size=GROUP_SIZE):
    """Returns (group armies, other armies, field marshal or None)."""
    land = {p for p in m.provinces if m.is_land(p)}
    ours = {me} | set(friends)
    own = {p for p in land if ctrl[p] in ours}
    mine = [d for d in divisions if d.owner == my_tag and d.province in land]
    fast = [d for d in mine if d.unit in fast_units]
    line = [d for d in mine if d.unit not in fast_units]

    # the piece of our land the offensive starts from: the one with the most divisions next to the target
    nb = {p: [n for n in m.neighbours.get(p, ()) if n in land] for p in land}
    comp = {}
    for p in own:
        if p in comp:
            continue
        stack, comp[p] = [p], p
        while stack:
            q = stack.pop()
            for n in nb[q]:
                if n in own and n not in comp:
                    comp[n] = p
                    stack.append(n)
    border = {p for p in own if any(ctrl[n] == target for n in nb[p])}
    # hops from the target's border, over our land
    dist = {p: 0 for p in border}
    queue = collections.deque(border)
    while queue:
        q = queue.popleft()
        for n in nb[q]:
            if n in own and n not in dist:
                dist[n] = dist[q] + 1
                queue.append(n)
    weight = collections.Counter(comp.get(d.province) for d in mine if d.province in border)
    main = weight.most_common(1)[0][0] if weight else None

    in_main = [d for d in line if comp.get(d.province) == main]
    rest = [d for d in line if comp.get(d.province) != main]
    in_main.sort(key=lambda d: dist.get(d.province, 999))
    front_line = in_main[:infantry_armies * army_size]
    rest = in_main[infantry_armies * army_size:] + rest

    group = []
    if fast:
        group.append(Army("fast", sorted(fast, key=lambda d: d.id)[:army_size]))
        rest += [d for d in fast if d not in group[0].divisions]
    if front_line:
        front_line.sort(key=lambda d: (m.provinces[d.province].y, m.provinces[d.province].x))  # north to south
        for i, chunk in enumerate(_chunks(front_line, army_size)[:group_size - len(group)]):
            group.append(Army("infantry %d" % (i + 1), chunk))
    placed = {d.id for a in group for d in a.divisions}
    others = []
    by_piece = collections.defaultdict(list)
    for d in rest:
        if d.id not in placed:
            by_piece[comp.get(d.province)].append(d)
    for piece, ds in by_piece.items():
        ds.sort(key=lambda d: (m.provinces[d.province].y, m.provinces[d.province].x))
        for chunk in _chunks(ds, army_size):
            others.append(Army("other", chunk))

    generals = sorted((l for l in leaders if not l.field_marshal), key=lambda l: (-l.attack, -l.planning))
    marshals = sorted((l for l in leaders if l.field_marshal), key=lambda l: (-l.attack, -l.planning))
    for army, leader in zip(group + others, generals):
        army.leader = leader
    marshal = marshals[0] if marshals else None
    return group, others, marshal
