"""Diplomacy: each country's pacts, wars, wargoals, opinion modifiers and faction.

DiplomacyLog(path, names, tags, others).step(date, lines) writes to diplomacy.tsv:
  date  change  country  kind  partner  key  value  was
kind      partner        key                value
pact      the other      the relation       FIRST>SECOND START (and the autonomy level of a puppet): guarantee,
          country        (guarantee,        military_access (FIRST has access to SECOND), market_access_rights,
                         puppet, ...)       puppet, war_relation (a war), improve_relation, lend_lease, ... (every
                                            block with first= and second=)
deal      the other      trade, truce_until the date (a dated relation without a block: trade, trade_equipment,
                                            truce_until after a country leaves or is kicked from a faction)
diplomat  the other      last_send_diplomat a day after a diplomatic action was last sent to it (improve relations,
          country                           a guarantee, a request, lend-lease, ...)
opinion   the other      the modifier       its value (only added and removed count, not the value going down)
wargoal   the target     the type           the wargoal's id; key justifying (no value) while one is being
                                            justified
faction   the leader     the name           the faction (one row per member, from a script query)
incoming  the sender     the action         a diplomatic action waiting for the country's answer
other     -              the key            any other part of the block, as one line
change: start (the first read of a country), new, changed, removed.

The relations are the `diplomacy={ active_relations={ TAG={ ... } } wargoals={ ... } }` and
`incoming_diplomatic_action={ ... }` parts of the country's save-format block (bridge.country); a relation between
two countries is in both countries' blocks. Factions are not in a country's block: one bridge.query
(`every_country = { limit = { is_in_faction = yes } ... }`, and the same for faction leaders) gives every member's
faction name. Reads at the first call and each new in-game day: TAGS each time, OTHERS() spread over FULL_DAYS
days, the factions each day; and TAGS and the factions at once after a diplomacy command in the record LINES
(COMMANDS: the actions hold their countries behind pointers, so the other side is read on its day).
"""
import os
import re

from hoi4ai import pdx
from hoi4ai import shared
from hoi4bridge import BridgeError, BridgeUnavailable, query

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tkind\tpartner\tkey\tvalue\twas"
COMMANDS = ("CDiplomaticActionCommand", "CIncomingDiplomaticActionActingCommand", "CSetAutoDiplomaticResponseCommand",
            "CCreateFactionCommand", "CAddFactionGoalCommand", "CAddFactionProgramCommand",
            "CRemoveFactionProgramCommand", "CSetFactionRuleCommand", "CEraseFactionRuleCommand",
            "CSetFactionUpgradeCommand", "CFactionSetCommanderCommand", "CAmendIncomingLendLeaseActionCommand",
            "CRequestExpeditionariesCommand", "CReleaseCountryCommand", "CPromoteAutonomyCommand",
            "CRemoveAutonomyCommand", "CDonePeaceConferenceCommand", "CSetNavalRegionAccessCommand")
SKIP = {"cached_sum", "attitude", "border_friction_claim", "rule_overrides"}
SKIP_TOP = {"active_relations", "wargoals", "proposed_diplo_action", "cached_allies_and_gurantees",
            "exile_army_leaders"}
QUIET = {"opinion"}     # kinds whose value changes don't count
_BLOCK = r"\n\t\t{}=\{{(.*?)\n\t\t\}}"
_DIPLOMACY = re.compile(_BLOCK.format("diplomacy"), re.S)
_INCOMING = re.compile(_BLOCK.format("incoming_diplomatic_action"), re.S)


def flat(v):
    """A parsed value as one line."""
    if not isinstance(v, list):
        return str(v)
    return " ".join(k if op is None and not isinstance(k, list) else
                    "{" + flat(k) + "}" if op is None else f"{k}={'{' + flat(x) + '}' if isinstance(x, list) else x}"
                    for k, op, x in v)


def relations(body):
    """{(kind, partner, key): value} from the text inside diplomacy={ }."""
    out = {}
    top = pdx.parse(body)
    for partner, _, rel in pdx.get(top, "active_relations") or ():
        if not isinstance(rel, list):
            continue
        for k, _, v in rel:
            if k in SKIP:
                continue
            if k == "opinion" and isinstance(v, list):
                mod = pdx.get(v, "modifier", "?")
                out[("opinion", partner, mod)] = pdx.get(v, "value", "")
            elif isinstance(v, list) and pdx.get(v, "first"):
                value = f"{pdx.get(v, 'first')}>{pdx.get(v, 'second')} {pdx.get(v, 'start_date', '')}".strip()
                if pdx.get(v, "autonomy_state"):
                    value += " " + pdx.get(v, "autonomy_state")
                out[("pact", partner, k)] = value
            elif k == "last_send_diplomat":
                out[("diplomat", partner, k)] = v
            elif not isinstance(v, list):
                out[("deal", partner, k)] = v
            else:
                out[("other", partner, k)] = flat(v)[:300]
    for kind, _, goal in pdx.get(top, "wargoals") or ():
        if isinstance(goal, list):
            gid = pdx.get(goal, "id")
            target = pdx.get(goal, "wargoaldata_recipient", "?")
            out[("wargoal", target, pdx.get(goal, "type", kind))] = pdx.get(gid, "id", "") if isinstance(gid, list) else ""
    for k, _, v in top:
        if k == "generate_wargoal_against":     # { "POL" ... }: the targets being justified against
            for target in pdx.values(v) if isinstance(v, list) else [v]:
                out[("wargoal", target, "justifying")] = ""
        elif k not in SKIP_TOP:
            out[("other", "", k)] = flat(v)[:300]
    return out


def incoming(body):
    out = {}
    for k, _, v in pdx.parse(body):
        if isinstance(v, list):
            sender = pdx.get(v, "sender") or pdx.get(v, "actor") or pdx.get(v, "from") or "?"
            out[("incoming", sender, k)] = flat(v)[:300]
        else:
            out[("incoming", "", k)] = v
    return out


def factions():
    """{tag: (faction name, leader tag)} of every country in a faction, or None when the query fails."""
    try:
        members = query('every_country = { limit = { is_in_faction = yes } log = "[THIS.GetTag]|[THIS.GetFactionName]" }')
        leaders = query('every_country = { limit = { is_faction_leader = yes } log = "[THIS.GetTag]|[THIS.GetFactionName]" }')
    except (BridgeError, BridgeUnavailable):
        return None
    lead = dict(reversed(ln.split("|", 1)) for ln in leaders if "|" in ln)
    return {t: (n, lead.get(n, "?")) for t, n in (ln.split("|", 1) for ln in members if "|" in ln)}


def is_command(line):
    p = line.split("\t", 3)
    return len(p) > 2 and p[2] in COMMANDS


class DiplomacyLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(kind, partner, key): value}
        self.members = None         # tag -> (name, leader)
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def read(self, tag):
        r = shared.country(tag)
        if not r or not r[0].startswith("OK"):
            return None
        text = "\n".join(r).replace("\r", "")
        m = _DIPLOMACY.search(text)
        if not m:
            return None
        out = relations(m.group(1))
        m = _INCOMING.search(text)
        if m:
            out.update(incoming(m.group(1)))
        return out

    @staticmethod
    def diff(tag, now, last):
        if last is None:
            return [("start", tag, k, v, "") for k, v in sorted(now.items())]
        rows = [("new" if k not in last else "changed", tag, k, v, last.get(k, ""))
                for k, v in sorted(now.items()) if k not in last or (last[k] != v and k[0] not in QUIET)]
        return rows + [("removed", tag, k, "", last[k]) for k in sorted(last) if k not in now]

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        acted = any(is_command(ln) for ln in lines)
        todo = list(self.tags) if acted else []
        newday = day != self.day
        if newday:
            self.days += 1
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
        for tag in dict.fromkeys(todo):
            now = self.read(tag)
            if now is None:
                continue
            last = self.held.get(tag)
            if last is not None:     # a quiet kind keeps its first value, so a slow change never adds up to a row
                now = {k: (last[k] if k[0] in QUIET and k in last else v) for k, v in now.items()}
            rows += self.diff(tag, now, last)
            self.held[tag] = now
        if newday or acted:
            f = factions()
            if f is not None:
                now = {t: {("faction", leader, name): name} for t, (name, leader) in f.items()}
                last = self.members
                for tag in sorted(set(now) | set(last or ())):
                    rows += self.diff(tag, now.get(tag, {}), None if last is None else last.get(tag, {}))
                self.members = now
        self.started = True
        for change, tag, (kind, partner, key), value, was in rows:
            self.f.write("\t".join((date, change, tag, kind, partner, key, str(value), str(was))) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
