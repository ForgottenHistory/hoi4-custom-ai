"""Take a country's land army away from the game's AI, so moves sent with bridge.move stick.

    python takeover.py TAG          # block the AI's army commands for TAG (do this before its first plans exist)
    python takeover.py --release TAG
    python takeover.py --status

The AI's plans (fronts, battle plans) move divisions themselves, so a division in a plan ignores bridge.move.
Blocking the commands that create plans and put divisions in them keeps every division free. Plans that already
exist keep running: take over at the start of a game (paused), before the AI has assigned anything.
"""
import argparse

import _bridge  # noqa: F401  (puts the hoi4-bridge client on the path)
from hoi4bridge import send

# Army commands the AI sends (from recordings of AI Germany). Owner found automatically (bridge.cmdblock auto rule).
ARMY_COMMANDS = [
    "COrderAssignCommand", "COrderUnassignCommand", "COrderNewFrontCommand", "COrderNewRootCommand",
    "COrderEditRootCommand", "COrderDeleteAllCommand", "COrderDeleteCommand", "COrderExecuteCommand",
    "COrderSetTrainingCommand", "COrderBlockSectionsCommand", "CSetOrderGroupExecutionTypeCommand",
    "CSetOrderGroupCohesionTypeCommand", "CEditAreaDefenseStateCommand", "CCreateAreaDefenseCommand",
    "CAssignToArmyGroupCommand", "CStrategicRedeploymentCommand", "CCancelMovementCommand", "CMoveCommand",
    "CMassMoveCommand",
    # these build the army structure (armies, army groups) in the country's theatre; owner via the theatre (67)
    "COrderGroupCommand", "CArmyGroupCommand",
]


def take(*tags):
    replies = send(*(f"bridge.cmdblock rule {c} auto" for c in ARMY_COMMANDS), "bridge.cmdblock add " + " ".join(tags))
    bad = [r for r in replies if not r.startswith("OK")]
    if bad:
        raise RuntimeError("; ".join(bad))


def release(*tags):
    send("bridge.cmdblock remove " + " ".join(tags))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="*")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.tags:
        (release if a.release else take)(*[t.upper() for t in a.tags])
    print(send("bridge.cmdblock")[0])


if __name__ == "__main__":
    main()
