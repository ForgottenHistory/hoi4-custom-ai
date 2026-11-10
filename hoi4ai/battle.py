"""A rough land battle estimate, after the wiki's combat rules (hoi4.paradoxwikis.com/Land_battle).

Each side is lumped into one block (summed attacks, defences, organisation and HP) and the fight is played hour by
hour with expected values, no dice:
- attacks per hour = (soft x (1 - target hardness) + hard x target hardness) / 10; the target's defences per hour =
  defence / 10 (breakthrough / 10 for the attacker). Attacks hit 10% while defences last, 40% after.
- each hit takes 0.053 x 2.5 organisation (x 3.5 if the target's armour isn't pierced) and 0.06 x 1.5 HP; a side's
  damage shrinks with its own HP, and with unpierced armour (the piercing table).
- the attacker gets the template terrain modifiers, river crossing (-30% / -60%), over-width and stacking
  penalties; low supply (under 50%) costs the attacker 25% attack, the defender 15% defence.
Divisions beyond the combat width wait in reserve: their organisation and HP refill the side as the front wears
down (their attacks don't add). Left out: entrenchment, forts, planning, generals, air, experience, divisions
retreating one by one. So it's a guide to which provinces are easy or hard, not a prediction.
"""

DEFAULT = {"soft_attack": 40, "hard_attack": 5, "defense": 250, "breakthrough": 40, "combat_width": 20,
           "max_strength": 200, "hardness": 0.0, "armor": 0.0, "piercing": 5.0, "terrain": {}}
RIVER = {0: 0.0, 1: -0.3, 2: -0.6}
MAX_HOURS = 24 * 10


def _pierce_factor(piercing, armor):
    """Damage multiplier for a side with PIERCING against ARMOR (the wiki's table)."""
    if armor <= 0 or piercing >= armor:
        return 1.0
    r = piercing / armor
    for limit, f in ((0.75, 0.8), (0.5, 0.65)):
        if r >= limit:
            return f
    return 0.5


class Side:
    def __init__(self, members):
        """MEMBERS: [(stats, org, org max, hp fraction)] with stats as templates.country gives them."""
        self.n = len(members)
        sums = {}
        for st, org, max_org, hp in members:
            for k in ("soft_attack", "hard_attack", "defense", "breakthrough", "combat_width"):
                sums[k] = sums.get(k, 0) + st.get(k, DEFAULT[k]) * (hp if k != "combat_width" else 1)
            sums["hp"] = sums.get("hp", 0) + st.get("max_strength", DEFAULT["max_strength"]) * hp
            sums["max_hp"] = sums.get("max_hp", 0) + st.get("max_strength", DEFAULT["max_strength"])
            sums["org"] = sums.get("org", 0) + org
            sums["max_org"] = sums.get("max_org", 0) + max(org, max_org)
        self.s = sums
        w = max(1, self.n)
        self.hardness = sum(st.get("hardness", 0) for st, *_ in members) / w
        self.armor = max((st.get("armor", 0) for st, *_ in members), default=0) * 0.3 + \
            sum(st.get("armor", 0) for st, *_ in members) / w * 0.7
        self.piercing = sum(st.get("piercing", DEFAULT["piercing"]) for st, *_ in members) / w


def fight(attackers, defenders, terrain, width, directions=1, river=0, att_supply=1.0, def_supply=1.0):
    """Estimated (attacker win chance 0..1, hours) of ATTACKERS against DEFENDERS (lists for Side) in TERRAIN with
    combat WIDTH, attacked from DIRECTIONS provinces across the worst RIVER (0 none, 1 small, 2 large)."""
    if not defenders:
        return 1.0, 0
    if not attackers:
        return 0.0, 0
    # each side fits what the width allows, best first; the rest wait in reserve and take the place of those worn
    # out (their organisation and HP join the fight, not their attacks)
    def line_up(members, key):
        used, front, reserve = 0, [], []
        for m in sorted(members, key=key, reverse=True):
            w = m[0].get("combat_width", DEFAULT["combat_width"])
            if front and used + w > width * 1.0:
                reserve.append(m)
                continue
            front.append(m)
            used += w
        return front, reserve, used
    front, a_res, used = line_up(attackers, lambda m: m[0].get("soft_attack", 0) * m[3])
    d_front, d_res, _ = line_up(defenders, lambda m: m[0].get("defense", 0) * m[3])
    a, d = Side(front), Side(d_front)
    ar, dr = Side(a_res), Side(d_res)
    att_mod = 1.0 + RIVER.get(river, 0)
    att_mod += sum(m[0].get("terrain", {}).get(terrain, {}).get("attack", 0) for m in front) / max(1, a.n)
    att_mod -= min(0.33, max(0, used / width - 1))  # over width
    att_mod -= 0.02 * max(0, a.n - (5 + 3 * (directions - 1)))  # stacking
    if att_supply < 0.5:
        att_mod -= 0.25
    def_mod = 1.0 + sum(m[0].get("terrain", {}).get(terrain, {}).get("defence", 0) for m in defenders) / max(1, d.n)
    def_def = 1.0 - (0.15 if def_supply < 0.5 else 0)
    att_mod, def_mod = max(0.1, att_mod), max(0.1, def_mod)

    ao, do_ = a.s["org"] + ar.s.get("org", 0), d.s["org"] + dr.s.get("org", 0)
    a_org, d_org = max(1, ao), max(1, do_)
    ahp, dhp = a.s["hp"] + ar.s.get("hp", 0), d.s["hp"] + dr.s.get("hp", 0)
    a_full, d_full = max(1, a.s["hp"]), max(1, d.s["hp"])
    a_pierce, d_pierce = _pierce_factor(a.piercing, d.armor), _pierce_factor(d.piercing, a.armor)
    a_die = 3.5 if a.armor > d.piercing else 2.5  # organisation die of hits on the attacker
    d_die = 3.5 if d.armor > a.piercing else 2.5

    def hits(attacks, defences):
        return 0.1 * attacks if attacks <= defences else 0.1 * defences + 0.4 * (attacks - defences)

    hours = 0
    while ao > 0 and do_ > 0 and hours < MAX_HOURS:
        hours += 1
        ah, dh = min(1.0, ahp / a_full), min(1.0, dhp / d_full)  # damage output shrinks with HP (reserves refill it)
        a_att = (a.s["soft_attack"] * (1 - d.hardness) + a.s["hard_attack"] * d.hardness) / 10 * att_mod * ah
        d_att = (d.s["soft_attack"] * (1 - a.hardness) + d.s["hard_attack"] * a.hardness) / 10 * def_mod * dh
        on_d = hits(a_att, d.s["defense"] / 10 * def_mod * def_def * dh) * a_pierce
        on_a = hits(d_att, a.s["breakthrough"] / 10 * att_mod * ah) * d_pierce
        do_ -= on_d * 0.053 * d_die * 1000  # Division org is x1000
        ao -= on_a * 0.053 * a_die * 1000
        dhp -= on_d * 0.06 * 1.5
        ahp -= on_a * 0.06 * 1.5
    # how far ahead the winner is: what share of its starting organisation it still has
    af, df = max(0, ao) / a_org, max(0, do_) / d_org
    if do_ <= 0 and ao > 0:
        return 0.5 + 0.5 * af, hours
    if ao <= 0 and do_ > 0:
        return 0.5 - 0.5 * df, hours
    return 0.5 + 0.5 * (af - df), hours
