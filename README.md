# hoi4-custom-ai

An experiment: replace Hearts of Iron IV's army AI with our own, driven from Python through
[hoi4-bridge](../hoi4-bridge) (a `version.dll` that takes console and `bridge.*` commands over a named pipe).

Singleplayer only. Needs hoi4-bridge installed in the game and its `client/` next to this repo
(`../hoi4-bridge/client`), or `HOI4_BRIDGE_CLIENT` pointing at it.

## How it works

The game's AI and the player act through command objects (`CCommand` subclasses). hoi4-bridge can record,
block and send them:

1. **Take over** a country's land army at game start (`tools/takeover.py TAG`, paused, before the AI's first
   plans): drops the AI's army commands for that country (fronts, battle plans, assignments, armies, rail moves,
   movement cancels), so no division ends up in an AI army or plan.
2. **Protect** the divisions we move (`bridge.protect add ID..`): the AI still sends `CCancelMovementCommand`
   for them; the bridge refuses the game's CancelMovement / ClearPath for protected divisions.
3. **Read** the situation (`bridge.snap`): every division's owner, province, order, organisation, strength and
   template, in milliseconds.
4. **Act** (`bridge.move ID PROVINCE`): the same move command the game builds when the player drags a division.

Verified 2026-11-09: in observer mode with Germany taken over, a division ordered from Kiel to Berlin walked
all the way; the AI tried to cancel it 103 times and to build German armies 1526 times, all refused.

## Tools

| script | does |
|---|---|
| `tools/takeover.py TAG` / `--release TAG` | take a country's land army from the AI (or give it back) |
| `tools/cmdrec.py [--dir FOLDER] [--tag TAG]` | record every command the game executes, with the in-game date, and a daily snapshot of every division; Ctrl+C stops |
| `tools/analyze_cmdrec.py RECORD [--countries ..]` | per country: commands by kind, orders touched, where divisions are sent (moves, rail), assignment churn |
| `tools/analyze_snaps.py [SNAPSHOTS] [--countries ..]` | per country and day: divisions, strength, organisation, divisions moved, in plans, lost |

Recordings go in `recordings/` (not in git: tens of MB each).

## Findings so far (Germany vs Poland, 1939 start)

| | player | game AI |
|---|---|---|
| first fighting to Polish capitulation | 15 days | 36 days |
| commands per day | 5 | 33 |
| direct moves | 106 | 0 |
| new fronts drawn | a handful | 126 |
| plan assignments per division | ~1 | 34 (max 79) |
| rail moves | 36, to the Polish front | 264, many to the west |

The game AI never aims a division at a place: it works only through fronts and battle plans and keeps
reshuffling divisions between them. The player sets up a few plans and then concentrates divisions on chosen
provinces with direct moves.

## Layouts used (game 1.19.x)

- Division (`CArmy`, handle type 51; the handle's object is the full object + 0x10): owner +0x1c8, province
  `*(+0x1e0)+0xa4`, order `*(+0xb0)+0xc`, organisation +0x20, strength +0x30 (fixed point, 100000 = 100%),
  template name `*(+0x3a8)+0x20`. Full object: path at +0x2c0, destination +0x2b4.
- Order (front / battle plan / army, type 53): owner +0x3c. Theatre (type 67): no owner field; its armies'
  list at +0x80 (owner of the first), divisions' list at +0x68.
- `CMoveCommand`: embeds a `CUnitMoveAction` at +0x28: division handle +0x38, province path (ids) +0x40 /
  count +0x4c. Player moves use flags `1 2 1 0 0 0 0`.
