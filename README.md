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
| `tools/controller.py --tag GER --target POL` | run the country's army with our planner (below); logs to `recordings/controller_*` (`--dry-run --once` to just see a plan) |
| `tools/heatmap.py [--tag GER --target POL]` | one planning step on the live game (sends nothing), drawn as a PNG: route costs to the goal, the fast army's sector and way, infantry support points, our infantry/fast divisions and enemy strength per province (`recordings/heatmaps/`) |
| `tools/mapcheck.py [PROVINCE ..]` | build the map (through the active mods), print a province's neighbours, check it against the running game and count who controls what |
| `tools/takeover.py TAG` / `--release TAG` | take a country's land army from the AI (or give it back) |
| `tools/cmdrec.py [--dir FOLDER] [--tag TAG]` | record every command the game executes, with the in-game date, and a daily snapshot of every division; Ctrl+C stops |
| `tools/analyze_cmdrec.py RECORD [--countries ..]` | per country: commands by kind, orders touched, where divisions are sent (moves, rail), assignment churn |
| `tools/analyze_snaps.py [SNAPSHOTS] [--countries ..]` | per country and day: divisions, strength, organisation, divisions moved, in plans, lost |

Recordings go in `recordings/` (not in git: tens of MB each).

## Library (`hoi4ai/`)

Mods: nothing about the map or the countries is hard-coded. Map data comes from the files the game itself loads,
and live data from the running game's memory.

- `gamefiles.py`: the game's files, through the active mods. The mods are the ones the launcher wrote to
  `dlc_load.json` for the last launch, in that order: a later mod's file wins, and `replace_path` hides the
  base game's (and earlier mods') files in that folder. The game folder is taken from the running `hoi4.exe`.
- `mapdata.py`: provinces (land/sea/lake, terrain, continent, centre), neighbours (pixels touching in the
  `provinces.bmp` named by `default.map`, plus `adjacencies.csv` crossings, minus its `impassable` borders), shared
  border length, and states (provinces, victory points) from `history/states`. Built in ~1 s, cached in `.cache/`.
- `memory.py`: reads the game's memory (read-only) and names objects by their RTTI class. It starts from the
  game state (`CCurrentGameState`), using the static the exe keeps only when RTTI confirms it, else by class
  name.
- `divisions.py`: every division (owner, province, destination, organisation, strength, template) via `bridge.snap`.
- `units.py`: sub-unit types from `common/units` (through the mods). A division is fast when its main type (the
  one its counter shows) is armour, or mobile with a transport (motorised, mechanised) or no speed malus.
- `pdx.py`: a small parser for the game's script files.
- `front.py`: the planner, after how a player runs an offensive. Infantry holds every province facing the
  enemy, gathers its reserves at a few support points, and attacks locally where clearly stronger. The fast
  divisions form one breakthrough army: before the war it deploys at the start of the best route to the
  enemy's top victory point, at war it attacks along that route and keeps pushing on the cheapest way. A
  route's cost counts terrain (plains 1, urban 1.5, forest and hills 2, marsh and jungle 3, mountains 4) and
  defenders. Each connected piece of the territory our front runs along (our own, our subjects' and faction
  members') is planned on its own.
- `live.py`: every province's controller, owner and state in one pass (~35 ms).

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
  `*(+0x1e0)+0xa4`, main sub-unit type `*(+0x128)+0x68` (std::string), destination +0x2a4 (0 = none), order `*(+0xb0)+0xc`, organisation +0x20, strength +0x30 (fixed point, 100000 = 100%),
  template name `*(+0x3a8)+0x20`. Full object: path at +0x2c0, destination +0x2b4.
- Order (front / battle plan / army, type 53): owner +0x3c. Theatre (type 67): no owner field; its armies'
  list at +0x80 (owner of the first), divisions' list at +0x68.
- Provinces: `CCurrentGameState` +0x2b0 is a vector of `CProvince*` indexed by province id. `CProvince`: id
  +0xa4, `CState*` +0xc0, controller country id +0x188 (checked by changing a province's controller with
  `set_province_controller`), `CControllerArea*` +0xd0, `COwnerArea*` +0xd8. `CState`: id +0x58 (agrees with
  the files for all land provinces), owner +0xc8, controller +0xcc.
- `CMoveCommand`: embeds a `CUnitMoveAction` at +0x28: division handle +0x38, province path (ids) +0x40 /
  count +0x4c. Player moves use flags `1 2 1 0 0 0 0`.
