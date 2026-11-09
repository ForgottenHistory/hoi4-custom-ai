"""Record a war the same way whoever fights it (a player, the game's AI, our controller), for tools/compare_runs.py.

A recording is a folder:
  meta.json      who recorded what: label, country, target, country ids -> tags, when it started
  status.tsv     per step: date, at war with the target, enemies, target capitulated
  divisions.tsv  per step: every division of the country and of its enemies (place, destination, organisation,
                 strength, supply, army, main unit, template)
  control.tsv    who controls each province: every province at the first step, then only the changes
Steps are in-game times; the caller decides how often (tools/battlerec.py: every few in-game hours).
"""
import datetime
import json
import os

import numpy as np


class BattleRecorder:
    def __init__(self, folder, label, tag, target, tags):
        """TAGS: {country id: tag} (controller.country_ids)."""
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        meta = os.path.join(folder, "meta.json")
        if not os.path.exists(meta):
            json.dump({"label": label, "tag": tag, "target": target, "tags": {str(k): v for k, v in tags.items()},
                       "started": datetime.datetime.now().isoformat(timespec="seconds")},
                      open(meta, "w", encoding="utf-8"), indent=1)
        self._status = self._open("status.tsv", "date\tat_war\tenemies\tcapitulated")
        self._divs = self._open("divisions.tsv", "date\tid\towner\tprovince\tdest\torg\tstrength\tsupply\tarmy"
                                                 "\tunit\ttemplate")
        self._ctrl = self._open("control.tsv", "date\tprovince\tcontroller")
        self._last = None
        self.tag = tag

    def _open(self, name, header):
        path = os.path.join(self.folder, name)
        new = not os.path.exists(path)
        f = open(path, "a", encoding="utf-8")
        if new:
            f.write(header + "\n")
        return f

    def step(self, when, divs, controller, enemies, at_war, capitulated):
        """WHEN: in-game datetime; DIVS: Snapshotter divisions; CONTROLLER: numpy array province -> country id;
        ENEMIES: tags at war with us."""
        date = when.strftime("%Y-%m-%d %H:00")
        self._status.write(f"{date}\t{int(at_war)}\t{' '.join(sorted(enemies))}\t{int(capitulated)}\n")
        keep = set(enemies) | {self.tag}
        for d in divs:
            if d.owner in keep:
                self._divs.write(f"{date}\t{d.id}\t{d.owner}\t{d.province}\t{d.dest}\t{d.org}\t{d.strength}"
                                 f"\t{d.supply}\t{d.army}\t{d.unit}\t{d.template}\n")
        ctrl = np.asarray(controller)
        changed = np.arange(len(ctrl)) if self._last is None else np.nonzero(ctrl != self._last)[0]
        for p in changed:
            if p and (self._last is not None or ctrl[p]):
                self._ctrl.write(f"{date}\t{p}\t{ctrl[p]}\n")
        self._last = ctrl.copy()
        for f in (self._status, self._divs, self._ctrl):
            f.flush()
