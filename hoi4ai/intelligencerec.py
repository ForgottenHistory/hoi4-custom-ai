"""Intelligence: each country's intelligence agency, its operatives and their missions, cryptology and operations.

IntelligenceLog(path, names, tags, others).step(date, lines) writes to intelligence.tsv:
  date  change  country  kind  key  value  was
kind         key            value
agency       the setting    name, icon, is_created, defense, max_operative_count, usable_operative_slots,
                            upgrade (the one being built), ... (any other part of the agency, as one line)
upgrade      the upgrade    its level (upgrade_form_department=1, ...)
operative    the name       MISSION>TARGET skill=N traits=... (an operative of the agency)
recruitable  the name       traits=... (an operative the country can recruit)
cryptology   the target     decryption=yes while decrypting it (also any other part of cryptology but its intel
                            source, as one line)
operation    TYPE>TARGET    a running operation: auto_commence=, completed=, operatives=N, phases=...
operation    finished TYPE  the targets it was done against, and how often (FRA 1)
operation    priority       the operations priority
change: start (the first read of a country), new, changed, removed.

All of it is the `intelligence_agency={ ... operative={...} recruitment={ recruitable_operatives={...} }
cryptology={...} }` and `operations={ finished={...} priority= ... }` parts of the country's save-format block
(bridge.country). Every country has an agency block, also before it builds one (is_created=yes once it has).
Progress that moves by itself is left out: `building`, `upgrade_progress`, `elapsed_days_for_next_slot`, an
operative's experience and trait progress, its mission's state and the amount decrypted. Reads at the first call and each new in-game day:
TAGS each time, OTHERS() spread over FULL_DAYS days; and TAGS at once after an intelligence command in the record
LINES (COMMANDS).
"""
import os
import re

from hoi4ai import pdx
from hoi4ai import shared

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\tkind\tkey\tvalue\twas"
COMMANDS = ("CBecomeSpyMasterCommand", "CCreateOperationCommand", "CDeleteOperationCommand", "CDismissOperativeCommand",
            "CHideDecryptionCommand", "CIntelligenceAgencyCancelCreationCommand",
            "CIntelligenceAgencyCancelUpgradeCommand", "CIntelligenceAgencyCreationCommand",
            "CIntelligenceAgencyQueueUpgradeCommand", "CIntelligenceAgencyUpgradeCommand", "CLaunchOperationCommand",
            "CRecruitOperativeCommand", "CReserveOperativeForOperationCommand", "CSetCountryOperationsPriorityCommand",
            "CSetIntelligenceAgencyLogoCommand", "CSetIntelligenceAgencyNameCommand",
            "CSetIntelligenceAgencyRandomHistoricalNameCommand", "CSetOperationAutoCommenceCommand",
            "CSetOperationAutoRepeatCommand", "CSetOperationTargetCommand", "CSetOperativeCodenameCommand",
            "CSetOperativeMissionCommand", "CStartStopDecryptionCommand")
VOLATILE = {"building", "upgrade_progress", "elapsed_days_for_next_slot", "experience", "in_progress", "state",
            "intel_source", "amount"}
_BLOCK = r"\n\t\t{}=\{{(.*?)\n\t\t\}}"
_AGENCY = re.compile(_BLOCK.format("intelligence_agency"), re.S)
_OPERATIONS = re.compile(_BLOCK.format("operations"), re.S)


def flat(v):
    """A parsed value as one line, without the VOLATILE keys."""
    if not isinstance(v, list):
        return str(v)
    return " ".join(k if op is None and not isinstance(k, list) else
                    "{" + flat(k) + "}" if op is None else f"{k}={'{' + flat(x) + '}' if isinstance(x, list) else x}"
                    for k, op, x in v if isinstance(k, list) or k not in VOLATILE)


def traits(op):
    t = pdx.get(op, "traits")
    return " ".join(pdx.values(t)) if isinstance(t, list) else (t or "")


def operative(op):
    """MISSION>TARGET skill=N traits=... of an operative's block."""
    parts = []
    mission = pdx.get(op, "mission")
    for kind, _, m in mission if isinstance(mission, list) else ():
        parts.append(f"{kind}>{pdx.get(m, 'target', '') if isinstance(m, list) else m}")
    parts.append(f"skill={pdx.get(op, 'skill', '')}")
    if traits(op):
        parts.append(f"traits={traits(op)}")
    return " ".join(parts)


def agency(body):
    """{(kind, key): value} from the text inside intelligence_agency={ }."""
    out = {}
    for k, _, v in pdx.parse(body):
        if k in VOLATILE:
            continue
        if k == "operative" and isinstance(v, list):
            out[("operative", pdx.get(v, "name", "?"))] = operative(v)
        elif k == "recruitment" and isinstance(v, list):
            for op, opr, _ in pdx.get(v, "recruitable_operatives") or []:
                if opr is None and isinstance(op, list):     # { { name= ... } { ... } }
                    out[("recruitable", pdx.get(op, "name", "?"))] = f"traits={traits(op)}"
            for k2, _, v2 in v:
                if k2 != "recruitable_operatives":
                    out[("agency", "recruitment." + k2)] = flat(v2)[:300]
        elif k == "cryptology" and isinstance(v, list):
            for k2, _, v2 in v:
                if k2 == "targets" and isinstance(v2, list):     # { { target="FRA" decryption=yes amount=25 } }
                    for t, op, _ in v2:
                        if op is None and isinstance(t, list):
                            out[("cryptology", pdx.get(t, "target", "?"))] = flat(
                                [x for x in t if x[0] != "target"])[:300]
                elif k2 not in VOLATILE:
                    out[("cryptology", k2)] = flat(v2)[:300]
        elif k == "upgrades" and isinstance(v, list):    # { upgrade_form_department=1 ... }
            for k2, _, v2 in v:
                out[("upgrade", k2)] = flat(v2)
        else:
            key = ("agency", k)     # a repeated key (upgrade=, ...) gets all its values
            out[key] = (out[key] + " | " if key in out else "") + flat(v)[:300]
    return out


def operation(op):
    """The settings of a running operation's block: its phases, operatives, auto commence, completed."""
    parts = [f"{k}={v}" for k in ("auto_commence", "auto_repeat", "completed") if (v := pdx.get(op, k))]
    slots = pdx.get(op, "operative_slots")
    if isinstance(slots, list):
        parts.append(f"operatives={sum(1 for x in slots if isinstance(x[0], list))}")
    phases = pdx.get(op, "phases")
    if isinstance(phases, list):
        parts.append("phases=" + " ".join(pdx.values(phases)))
    return " ".join(parts)


def operations(body):
    """{(kind, key): value} from the text inside operations={ }: running operations keyed TYPE>TARGET, finished
    ones as their count per target."""
    out = {}
    for k, _, v in pdx.parse(body):
        if k == "running" and isinstance(v, list):
            for kind, _, op in v:
                if isinstance(op, list):
                    out[("operation", f"{kind}>{pdx.get(op, 'target', '?')}")] = operation(op)
        elif k == "finished" and isinstance(v, list):
            for kind, _, done in v:     # operation_infiltrate_civilian={ FRA 1 }
                out[("operation", f"finished {kind}")] = flat(done)
        elif k not in VOLATILE:
            out[("operation", k)] = flat(v)[:300]
    return out


def is_command(line):
    p = line.split("\t", 3)
    return len(p) > 2 and p[2] in COMMANDS


class IntelligenceLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> {(kind, key): value}
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
        m = _AGENCY.search(text)
        out = agency(m.group(1)) if m else {}
        m = _OPERATIONS.search(text)
        if m:
            out.update(operations(m.group(1)))
        return out

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        todo = list(self.tags) if any(is_command(ln) for ln in lines) else []
        if day != self.day:
            self.days += 1
            todo += self.tags
            todo += list(self.others() or ()) if not self.started else shared.part(self.others(), self.days, FULL_DAYS)
        self.day = day
        rows = []
        for tag in dict.fromkeys(todo):
            now, last = self.read(tag), self.held.get(tag)
            if now is None:
                continue
            if last is None:
                rows += [("start", tag, k, v, "") for k, v in sorted(now.items())]
            else:
                rows += [("new" if k not in last else "changed", tag, k, v, last.get(k, ""))
                         for k, v in sorted(now.items()) if last.get(k) != v]
                rows += [("removed", tag, k, "", last[k]) for k in sorted(last) if k not in now]
            self.held[tag] = now
        self.started = True
        for change, tag, (kind, key), value, was in rows:
            self.f.write("\t".join((date, change, tag, kind, key, str(value), str(was))) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
