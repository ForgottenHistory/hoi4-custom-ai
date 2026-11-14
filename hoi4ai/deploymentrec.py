"""Training and deployment: each country's deployment orders (the divisions it trains) and their lines.

DeploymentLog(path, names, tags, others).step(date, lines) writes to deployment.tsv:
  date  change  country  order  line  template  name  amount  location  priority  training  was
An order row (line empty) is one deployment order (handle 64, order its id): template the division template id, name
the template's name, amount the order's own amount field (0 in every order seen), location the province the
divisions deploy to, priority 0 low / 1 normal / 2 high. A line row is a division (or several of the same name run)
in training under an order (handle 63, line its id): amount how many divisions, training the training it has
(written, not compared). change: start (the first read of a country), new, changed, removed, deployed. was: what
changed, as field=old (e.g. "priority=1", "amount=3").
  deployed: a division left a line for the field (deployed early by the player, or done training): the line's next
      division has another name, or the line is gone after a CDeployConveyorLineCommand or without any command (a
      line taken away by the player is removed). training is the training it had at the last read.
Reads at the first call and each new in-game day: TAGS (the recorded countries) each time, OTHERS() (a callable, e.g.
MioLog's producing countries) every FULL_DAYS days, the recorded countries at once after a new order (its country
isn't in the command) and a country at once after a command on one of its orders or lines.

Read from a country's save-format block:
  deployment={ military_deployment_conveyor={ id={ id=N type=64 } division_template_id={ id=T type=52 } name=...
      amount location priority role closed military_deployment_line={ id={ id=L type=63 }
      division_name={ type name_order } amount military_deployment={ priority training max_training
      army_manpower_value army_manpower_need equipment={ ... } max_manpower } } ... } }
The player's commands (in command_record.tsv; the owner column is empty, the AI uses the same ones):
  CCreateConveyorCommand          division template 52:ID +0x28 (a new order)
  CAddConveyorLineCommand         order 64:ID +0x28, how many +0x30 (more divisions)
  CRemoveConveyorLineCommand      line 63:ID +0x28 (fewer divisions)
  CSetConveyorPriorityCommand     order 64:ID +0x28, priority +0x30
  CSetConveyorLocationCommand     order 64:ID +0x28, province id +0x30
  CChangeConveyorTemplateCommand  order 64:ID +0x28, template 52:ID +0x30
  CDeployConveyorLineCommand      line 63:ID +0x28 (deploy early)
  CRemoveConveyorCommand          order 64:ID +0x28 (cancel)
"""
import os
import re

from hoi4bridge import send
from hoi4ai.logisticsrec import _block

FULL_DAYS = 7
HEADER = "date\tchange\tcountry\torder\tline\ttemplate\tname\tamount\tlocation\tpriority\ttraining\twas"
CREATE = ("CCreateConveyorCommand",)
COMMANDS = CREATE + ("CAddConveyorLineCommand", "CRemoveConveyorLineCommand", "CSetConveyorPriorityCommand",
                     "CSetConveyorLocationCommand", "CChangeConveyorTemplateCommand", "CDeployConveyorLineCommand",
                     "CRemoveConveyorCommand")
ORDER_FIELDS = ("template", "name", "amount", "location", "priority")
LINE_FIELDS = ("amount", "name_order")
_ID = re.compile(r"id=\{ id=(\d+) type=(\d+) \}")


def _field(text, key):
    m = re.search(r"\n\t*" + key + r"=(\"[^\"]*\"|[^\s{}]*)", text)
    return m.group(1).strip('"') if m else ""


def orders(text):
    """{(order id, line id or ""): {field: value}} of one save-format country block."""
    out = {}
    for m in re.finditer(r"military_deployment_conveyor=\{", text):
        blk = _block(text, m.start())
        i = blk.find("military_deployment_line=")
        head = blk if i < 0 else blk[:i]
        oid = _ID.search(head)
        tpl = re.search(r"division_template_id=\{ id=(\d+)", head)
        if not oid:
            continue
        order = oid.group(1)
        out[(order, "")] = {"template": tpl.group(1) if tpl else "", "name": _field(head, "name"),
                            "amount": _field(head, "amount"), "location": _field(head, "location"),
                            "priority": _field(head, "priority")}
        for lm in re.finditer(r"military_deployment_line=\{", blk):
            lb = _block(blk, lm.start())
            lid = _ID.search(lb)
            if not lid:
                continue
            dep = lb.find("military_deployment=")
            top = lb if dep < 0 else lb[:dep]
            rest = "" if dep < 0 else lb[dep:]
            out[(order, lid.group(1))] = {"amount": _field(top, "amount"), "name_order": _field(top, "name_order"),
                                          "training": _field(rest, "training") or "0"}
    return out


def parse(line):
    """A record line -> (class, handle id at +0x28) of a deployment command, or None."""
    p = line.rstrip("\n").split("\t")
    if len(p) < 8 or p[2] not in COMMANDS:
        return None
    b = bytes.fromhex(p[7])
    return (p[2], str(int.from_bytes(b[0x2c:0x30], "little"))) if len(b) >= 0x30 else None


class DeploymentLog:
    def __init__(self, path, names=None, tags=(), others=lambda: ()):
        from hoi4ai.eventrec import Names
        self.names = names or Names()
        self.tags = [t for t in tags if t and t != "none"]
        self.others = others
        self.held = {}              # tag -> orders()
        self.owner = {}             # ("64" or "63", id) -> tag
        self.deploys, self.taken = set(), set()     # line ids the player deployed early / took away
        self.started = False
        self.day, self.days = None, 0
        new = not os.path.exists(path)
        self.f = open(path, "a", encoding="utf-8")
        if new:
            self.f.write(HEADER + "\n")

    def read(self, tag):
        r = send(f"bridge.country {tag}")
        return orders("\n".join(r)) if r and r[0].startswith("OK") else None

    def _todo(self, lines):
        todo = []
        for c in filter(None, map(parse, lines)):
            cls, i = c
            if cls in CREATE:
                todo += self.tags
                continue
            kind = "63" if cls in ("CRemoveConveyorLineCommand", "CDeployConveyorLineCommand") else "64"
            if cls == "CDeployConveyorLineCommand":
                self.deploys.add(i)
            elif cls == "CRemoveConveyorLineCommand":
                self.taken.add(i)
            if (kind, i) in self.owner:
                todo.append(self.owner[(kind, i)])
        return todo

    def _rows(self, tag, now, last):
        rows = []
        for (order, line), v in now.items():
            if line:
                cols = (order, line, now[(order, "")]["template"], now[(order, "")]["name"], v["amount"], "", "",
                        v["training"])
                fields = LINE_FIELDS
            else:
                cols = (order, line) + tuple(v[f] for f in ORDER_FIELDS) + ("",)
                fields = ORDER_FIELDS
            old = None if last is None else last.get((order, line))
            if last is None:
                rows.append(("start", tag) + cols + ("",))
            elif old is None:
                rows.append(("new", tag) + cols + ("",))
            elif any(old[f] != v[f] for f in fields):
                was = ",".join(f"{f}={old[f]}" for f in fields if old[f] != v[f] and f != "name_order")
                if line and old["name_order"] != v["name_order"]:
                    rows.append(("deployed", tag) + cols[:7] + (old["training"], was))
                else:
                    rows.append(("changed", tag) + cols + (was,))
        for (order, line), v in (last or {}).items():
            if (order, line) in now:
                continue
            if line:
                tpl = last.get((order, ""), {})
                gone = (order, "") not in now or line in self.taken
                change = "deployed" if line in self.deploys or not gone else "removed"
                rows.append((change, tag, order, line, tpl.get("template", ""), tpl.get("name", ""), v["amount"],
                             "", "", v["training"], ""))
            else:
                rows.append(("removed", tag, order, line) + tuple(v[f] for f in ORDER_FIELDS) + ("", ""))
        return rows

    def step(self, date, lines=()):
        """DATE as for ProductionLog.step. Returns the rows written."""
        day = date.split(",", 1)[1] if "," in date else date.split(" ", 1)[0]
        todo = self._todo(lines)
        if day != self.day:
            self.days += 1
            todo += self.tags
            if not self.started or self.days % FULL_DAYS == 0:
                todo += list(self.others() or ())
        self.day = day
        rows = []
        for tag in dict.fromkeys(todo):
            now = self.read(tag)
            if now is None:
                continue
            last = self.held.get(tag)
            rows += self._rows(tag, now, last)
            for order, line in last or ():
                self.owner.pop(("63", line) if line else ("64", order), None)
            for order, line in now:
                self.owner[("63", line) if line else ("64", order)] = tag
            self.held[tag] = now
        self.started = True
        for row in rows:
            self.f.write("\t".join((date,) + row) + "\n")
        self.f.flush()
        return len(rows)

    def close(self):
        self.f.close()
