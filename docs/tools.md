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
province (`hoi4ai/battlerec.py`), its battle plans when they changed (`plans.tsv`, `hoi4ai/planrec.py`), its state each day (`country/`, `country.tsv`, as cmdrec), the events answered (`events.tsv`), every country's division templates as they change (`division_templates.tsv`), every country's equipment designs and obsolete designs (`equipment_designs.tsv`) and production lines (`production_lines.tsv`), MIO sizes and traits (`mios.tsv`), market contracts and listings (`market.tsv`), every country's doctrines (`doctrines.tsv`), officer corps spirits (`spirits.tsv`), national focus, laws, advisors and national spirits (`politics.tsv`), construction lines (`construction.tsv`), trade deals and auto-import settings (`trade.tsv`), pacts, wars, wargoals and factions (`diplomacy.tsv`), occupation laws and garrison templates (`occupation.tsv`), decisions taken, ignored, running and available (`decisions.tsv`), the intelligence agency, operatives and operations (`intelligence.tsv`), fuel prioritization, army motorization and exercises (`logistics.tsv`), training and deployment (`deployment.tsv`), generals (`generals.tsv`), air wings (`air.tsv`), task forces and admirals (`naval.tsv`, `admirals.tsv`), research slots and finished techs (`research.tsv`), and every command the game executes (`bridge.cmdrec`, with the date stamped
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

Air in land combats (`battle_air.tsv`, `BattleLog`): each `CLandCombatant` (side) keeps its air wings in a vector at +0x140 (count +0x148) of `CAirInLandCombat`. Per entry: wing id at +0xc (same id as `air.tsv`), **number of support planes in the fight at +0x14** (checked against the combat window: 90 German and 37 Polish planes, both matched), +0x18 a value that falls during the fight (100000 for a fresh wing; meaning not decoded). Columns f10..f38 in `battle_air.tsv` are the raw ints at +0x10..; only f14 is understood. Not found: the window's cumulative CAS damage ("dealt 10 damage to Organization / 4 to Strength", planes shot down) and the "losses to enemy airplanes / ground forces" counters of the battle log. They are not in `CLandCombat`, its two sides or the air entries (searched ints, floats, doubles, x1000 values); `CCombatHistoryEntry` (317 objects, the battle list) was searched for the Katowice numbers without a hit. The sides do carry growing fixed-point counters (+0x178/+0x180/+0x168/+0x170/+0x220, cross-matching between the two sides, ~hours-of-combat ticks at +0x1c8/+0x1cc), unidentified. `+0x230` on a side jumped when a division left the battle. The report's "Air support in battles" lists each wing's battles, days and mean planes (labelled CAS value).

`battle_raw.tsv` (`CombatTrace` in `hoi4ai/countryrec.py`, own thread, ~1 s): the last raw bytes of every land combat before it ends (hex of the combat 0x200, both sides 0x400, each air entry 0xa0), because the counters die with the combat object. Decode from the file. Matched against the game's battle log for the Poznan fight (province 11558; Germany = side a, 93 support planes; log: Germany lost 1605 manpower to ground forces and 0 to air, Poland lost 905 to ground and 1347 to air, 2252 total): the cross-matching side counters are casualty damage in fixed point at about 3,300 per man: side a +0x178 (= side b +0x220) ended 5,373,600 (1605 men, 3348 per man) and side a +0x220 (= side b +0x178) 3,001,800 (905 ground men, 3317 per man). Side b +0x168 (4,342,826) and +0x180 (4,534,680) are about 3,224 and 3,366 per man of Poland's 1347 air losses, so one of them is probably air damage taken (they only grow on the side attacked by planes). The ratio is not constant (about +-3%), so a man count cannot be read exactly from them yet; a small fight (province 7459, Germany lost 9 men) gives 29,400 = 3,267 per man. One matched fight only: each further fight needs its log numbers and the city name to pin the formula. Not found anywhere in memory (ints, floats, doubles, x10..x10000, all regions): the log numbers themselves (1605, 905, 1347 near each other), so they are computed or sit in the finished combat's history record in another form. The window's CAS damage (10 org, 4 strength) is also still not located. +0xa8/+0xac/+0xc8/+0xcc on a side read 159 for the whole fight: not a counter.

Decoded side counters (3 fights matched to the game's battle log: Poznan, Zaolzie, Krakow; row 139 in ger_air_test, province 9427): a side's `+0x178` (= other side's `+0x220`) = **ground** casualty damage that side took, `+0x168` = **air** casualty damage that side took (grows only on the side planes attack). Men lost = counter / F with F = 3,065 to 3,383 between fights (Krakow Germany: ground 2,928,900 / 866 = 3,382 and air 362,001 / 107 = 3,383, the same F on one side of one fight; Poland 3,065 and 3,136 in the same fight), so the report's "Battle casualties (estimated)" section uses F = 3,300 and says it is good to ~5%. `battle_raw.tsv` has a `date` column (the date loop's day; older rows empty), polled every 0.1 s. The window's CAS org/strength damage and the exact log numbers are still not found.

CAS damage in the combat window (Danzig fight, 12 readings of the window against a 0.5 s log of the counters): the organisation figure = side `+0x170` / 100,000 and the strength figure = side `+0x168` / 100,000, both rounded down, on the side the planes attack (the window's cumulative "organization / strength / planes" line). `+0x170` is about 2x `+0x168`. Side `+0x180` is about 1.35x the ground damage taken (`+0x178`), not CAS. Planes shot down are still not found.

## Recording and analysis tools

These moved to the [hoi4-recorder](../../hoi4-recorder) project (`cmdrec.py`, `report.py`, `rows.py`, `examples.py`,
`campaignmap.py`, `apm.py` and the rest; see its `docs/tools.md`). `battlerec.py` here uses its `hoi4rec` recorders.
