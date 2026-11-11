# Tools

All scripts are in `tools/` and run against the live game through hoi4-bridge. Run them from the repo root.
The ones that draw maps write a PNG and open it unless given `--no-open`. Run any script with `--help` for the
full options.

## Running a country

### `controller.py`

    python tools/controller.py --tag GER --target POL [--organize] [--armies] [--dry-run --once]

Runs a country's army with our planner (`hoi4ai/front.py`) instead of the game's AI. Start a new game,
`observe` (or play another country), pause, then run it. It takes the country's army from the AI
(`takeover.py`), then every `--hours` in-game hours reads every division and who controls each province, plans
(hold the border, mass reserves at breakthrough points, attack where we're strong enough) and sends the moves.

- `--organize` first builds the army structure (`hoi4ai/organize.py`): one fast army under the best attacking
  general, up to 4 infantry armies of at most 24 along the front, an army group of these under the best field
  marshal, and armies for the rest.
- `--armies` lets the game form armies and pick generals itself (see `takeover.py`).
- `--dry-run --once` prints one plan and sends nothing.
- Logs to `recordings/controller_*`.

### `takeover.py`

    python tools/takeover.py [--armies] TAG
    python tools/takeover.py --release TAG

Takes a country's land army away from the game's AI so moves sent with `bridge.move` stick. The AI's plans
(fronts, battle plans) move divisions themselves, so a division in a plan ignores `bridge.move`. Blocking the
commands that create plans and put divisions in them keeps every division free. Plans that already exist keep
running, so take over at the start of a game (paused), before the AI has assigned anything.

`--armies` lets the game still form armies and pick generals and field marshals (their buffs), with fronts and
plans blocked. `--release` gives the army back.

### `ai_only.py`

    python tools/ai_only.py GER SOV     # every other country stops (no research, production, diplomacy, moves)
    python tools/ai_only.py --all       # AI back on for every country

Uses the console's `ai TAG`, which toggles one country's AI and says which way it went, so a country that ends
up the wrong way is toggled again. Run it after loading a save: the console setting is probably not saved.
Countries with their AI off still exist: their divisions defend where they stand, events still fire, and others'
diplomatic requests to them may wait unanswered.

## Seeing what the planner sees

None of these send anything to the game.

| script | draws |
|---|---|
| `heatmap.py [--tag GER --target POL]` | one planning step on the live game: route costs to the goal, the fast army's sector and way, infantry support points, our infantry/fast divisions and enemy strength per province (`recordings/heatmaps/`) |
| `oddsmap.py [--tag GER] [--target POL]` | how likely an attack on each enemy province is to win. For every enemy province with defenders next to ours, all our divisions next to it attack at once and `hoi4ai/battle.py` estimates the fight from both sides' template stats, organisation, strength, supply, terrain, combat width and rivers. Green = likely win, red = likely loss. A rough guide only |
| `planmap.py [--tag GER] [--target POL]` | the battle plans as the game holds them: each army's front lines on the border (left) next to where its divisions stand (right) |
| `defence.py [--tag POL] [--attacker GER] [--with SLO ..]` | where a country could defend: candidate lines from the border inwards (victory points and factories kept, provinces, divisions needed), the chosen main and fallback lines, on a map of how hard each of our provinces is for the attacker to reach (`hoi4ai/defence.py`) |

## Recording and comparing wars

### `battlerec.py`

    python tools/battlerec.py --label player [--tag GER] [--target POL] [--hours 4] [--war-days 183] [--no-cmdrec]

Records a war while you (or the game's AI) fight it. Start the game (play the country, or `observe`), then run
this. Every `--hours` in-game hours it writes every division of the country and its enemies and who controls each
province (`hoi4ai/battlerec.py`), and every command the game executes (`bridge.cmdrec`, with the date stamped
in). It stops a step after the target capitulates, `--war-days` after the declaration, or on Ctrl+C. The
controller records the same way by itself. Output: `recordings/battle_<label>_<TAG>_<time>/`.

### `compare_runs.py`

    python tools/compare_runs.py RUN_FOLDER [RUN_FOLDER ...] [--cities Danzig Warsaw ...] [--json OUT]

Compares recorded wars side by side. Per run: days from the declaration to the target's capitulation, when each
victory point fell (the `--cities`, else the four most valuable), provinces taken per day, enemy divisions
destroyed, and how the army fought: share of divisions attacking, how many divisions each attack had, how far the
fast divisions drove, supply, strength and organisation, divisions idle behind the front, and (with a command
record) what commands the country sent. Province names come from the game's localisation, so mods' maps work.

### `runmap.py`

    python tools/runmap.py RUN_FOLDER [RUN_FOLDER ...] [--day 15 [DAY ...]] [--war-start "1939-08-18 17:00"]
                           [--cols 4] [--gif] [--labels You "Game AI" ...] [--out FILE] [--no-open]
                           [--around HOL BEL LUX] [--all-enemies] [--hops 12]

Maps of recorded wars at the same day of each: land taken from the target, the target's land still held, our
divisions (fast and infantry apart, with how many stand in a province) and the target's, and its most valuable
victory points. One panel per run and day, at the snapshot nearest to DAY days after the declaration (the run's
first step at war, or `--war-start` when the run began after it).

- `--cols N` panels per row; `--gif` also writes an animation.
- `--around TAGS` crops on those countries' land instead of the target's.
- `--all-enemies` counts every country at war with us at the declaration as the enemy.
- `--hops N` crops on enemy land within N provinces of our border (for a big country's front).

## Recording commands

### `cmdrec.py`

    python tools/cmdrec.py [--dir FOLDER] [--tag TAG]

Records every command the game executes (player and AI) while you play: turns on `bridge.cmdrec`, keeps stamping
the in-game date into the record until Ctrl+C, which turns recording off. Each new in-game day it also snapshots
every division (`bridge.snap`) into `unit_snapshots.tsv`: owner, province, order, organisation, strength,
template.

### `analyze_cmdrec.py`

    python tools/analyze_cmdrec.py RECORD [--countries ..]

Compares how countries play, from a `bridge.cmdrec` record. Per country (the player first, then the given
countries or the busiest AIs): commands per in-game day by class, what each did with its orders (fronts /
battle plans), where divisions are sent (moves, rail) and assignment churn. Also lists the classes whose owner
couldn't be found, so owner rules can be added for them.

### `analyze_snaps.py`

    python tools/analyze_snaps.py [SNAPSHOTS] [--countries ..]

Daily division snapshots (`unit_snapshots.tsv`) to how each country's army fared. Per country and day:
divisions, mean strength, mean organisation (as a share of the highest seen for that division), divisions that
moved since the last snapshot, divisions under a front or battle plan, and divisions lost since the first
snapshot.

## Checking the data

### `mapcheck.py`

    python tools/mapcheck.py [PROVINCE ..]

Builds the map from the game files (through the active mods) and checks it against the running game. Prints the
mods in use, the map size, and for each PROVINCE its neighbours. With the game running, checks every province's
state against the files and lists who controls how many provinces. Use this first if the planner seems to see
the wrong map.

### `templates.py`

    python tools/templates.py [TAG] [--json OUT]

A country's division templates in the running game and how they fight. Per template: how many divisions use it,
its battalions and support companies, the equipment its divisions hold, soft and hard attack, speed and combat
width (averages over its divisions, with the range when divisions differ), and its terrain modifiers.
