# The planner and the library

## How the planner decides (`hoi4ai/front.py`)

Modelled on how a player runs an offensive.

- **Infantry** holds every province facing the enemy. Half its reserves back the fast army (its shoulders); half
  go to a few support points, filling nearby front provinces before going deep. It attacks locally where clearly
  stronger.
- **Fast divisions** form one breakthrough army. Before the war it deploys at the start of the best route to the
  enemy's victory points; at war it attacks along that route and keeps pushing on the cheapest way. It only goes
  for the enemy's main body (its largest connected piece of land); pockets are left to the infantry.
- **A division is "fast"** when its main type (the one its counter shows) is armour, or mobile with a transport
  (motorised, mechanised) or no speed malus.
- **Route cost** counts terrain (plains 1, urban 1.5, forest and hills 2, marsh and jungle 3, mountains 4),
  defenders, and rivers crossed. A route ends at the victory point with the lowest cost plus 8 / (1 + VP / 5), so
  bigger victory points pull harder (+1 small, +2 large; attacks across them want 25% / 50% more strength).
- **Stacking:** no more than 6 divisions are planned per province. A bigger force spreads to the provinces behind
  its point, nearest first (big stacks starve: supply, attrition).
- **Support points** for infantry only go on routes at most 1.2x the best cost; otherwise the infantry stands
  behind the fast army.
- **Disconnected territory:** each connected piece of the territory our front runs along (our own, our subjects'
  and faction members') is planned on its own.

## Modules (`hoi4ai/`)

Mods: nothing about the map or the countries is hard-coded.

| module | does |
|---|---|
| `gamefiles.py` | the game's files, through the active mods. The mods are the ones the launcher wrote to `dlc_load.json` for the last launch, in that order: a later mod's file wins, and `replace_path` hides the base game's (and earlier mods') files in that folder. The game folder is taken from the running `hoi4.exe` |
| `mapdata.py` | provinces (land/sea/lake, terrain, continent, centre), neighbours (pixels touching in the `provinces.bmp` named by `default.map`, plus `adjacencies.csv` crossings, minus its `impassable` borders), shared border length, river borders (`rivers.bmp`: a border with a river along at least a quarter of it; large from width index 7), and states (provinces, victory points) from `history/states`. Built in about a second, cached in `.cache/` |
| `memory.py` | reads the game's memory (read-only) and names objects by their RTTI class. It starts from the game state (`CCurrentGameState`), using the static the exe keeps only when RTTI confirms it, else by class name |
| `live.py` | every province's controller, owner and state in one pass (~35 ms) |
| `divisions.py` | every division (owner, province, destination, organisation, strength, template) via `bridge.snap` |
| `units.py` | sub-unit types from `common/units` (through the mods) |
| `templates.py` | a country's division templates, read live, and what each fights like |
| `leaders.py` | a country's generals and field marshals from the game (name, kind, attack, defence, planning, logistics) |
| `organize.py` | the army structure a player sets up (one field marshal over a fast army and infantry armies) |
| `front.py` | the planner (above) |
| `plans.py` | battle plans for our armies, as a player draws them: a front line on a stretch of the border and an offensive line to a nearby victory point. Divisions on a plan build up the planning bonus, which plain move orders never get. With the army's cohesion set to immobile the game doesn't move them along the plan, so our own orders keep moving them while planning builds |
| `battle.py` | a rough land battle estimate after the wiki's combat rules, played hour by hour with expected values |
| `defence.py`, `defend.py` | where a country could hold a line against an attacker, and running that defence |
| `battlerec.py` | recording a war the same way whoever fights it (player, game AI, our controller) |
| `wars.py` | facts about a war from the game's own war summary (casualties) |
| `pdx.py` | a small parser for the game's script files |
