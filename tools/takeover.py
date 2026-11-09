"""Take a country's land army away from the game's AI, so moves sent with bridge.move stick.

    python takeover.py TAG          # block the AI's army commands for TAG (do this before its first plans exist)
    python takeover.py --armies TAG # the same, but let the game form armies and pick their generals
    python takeover.py --release TAG
    python takeover.py --status

The AI's plans (fronts, battle plans) move divisions themselves, so a division in a plan ignores bridge.move.
Blocking the commands that create plans and put divisions in them keeps every division free. Plans that already
exist keep running: take over at the start of a game (paused), before the AI has assigned anything.

With --armies the game may still organise divisions into armies and army groups and give them generals and
field marshals (their skills and traits buff every division in the army); fronts and battle plans, the orders
that move divisions, stay blocked.
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
ARMY_STRUCTURE = ["COrderGroupCommand", "CArmyGroupCommand", "CAssignToArmyGroupCommand"]


def take(*tags, armies=False):
    """Block the AI's army commands for TAGS. armies=True leaves army structure (armies, generals) to the game."""
    rules = [f"bridge.cmdblock rule {c} {'off' if armies and c in ARMY_STRUCTURE else 'auto'}" for c in ARMY_COMMANDS]
    replies = send(*rules, "bridge.cmdblock add " + " ".join(tags))
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
    ap.add_argument("--armies", action="store_true", help="let the game form armies and pick generals")
    a = ap.parse_args()
    if a.tags:
        tags = [t.upper() for t in a.tags]
        release(*tags) if a.release else take(*tags, armies=a.armies)
    print(send("bridge.cmdblock")[0])


if __name__ == "__main__":
    main()
