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
province (`hoi4ai/battlerec.py`), its battle plans when they changed (`plans.tsv`, `hoi4ai/planrec.py`), its state each day (`country/`, `country.tsv`, as cmdrec), the events answered (`events.tsv`), every country's division templates as they change (`division_templates.tsv`), every country's equipment designs and obsolete designs (`equipment_designs.tsv`) and production lines (`production_lines.tsv`), MIO sizes and traits (`mios.tsv`), market contracts and listings (`market.tsv`), every country's doctrines (`doctrines.tsv`), officer corps spirits (`spirits.tsv`), fuel prioritization and army motorization (`logistics.tsv`), training and deployment (`deployment.tsv`), generals (`generals.tsv`), and every command the game executes (`bridge.cmdrec`, with the date stamped
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

What a recording holds, and what it doesn't:

- Decisions, as tables: the commands, events answered, division templates, equipment designs (modules, the design
  they're based on), production lines (design, factories, priority, MIO), MIO traits, market purchases and listings, doctrines, officer corps spirits, fuel prioritization and army motorization. A row says what was chosen,
  not what it does: a trait is its token, a design its modules.
- Results, as raw state: the recorded country's whole save-format block each day (`country/DAY.txt.gz`): output and
  efficiency of each production line, stockpile, MIO funds and size, research, manpower... Only a few headline
  numbers are pulled out (`country.tsv`); the rest has to be compared across days afterwards.
- Not recorded: what a choice means (a trait's bonus, a design's stats and cost). These follow from the game files
  (`common/military_industrial_organization`, equipment modules, localisation for the in-game names) and can be
  decoded afterwards. Other countries' state is also not kept: their decisions are logged (lines, designs,
  templates, market contracts, doctrines; MIOs, sale listings, spirits and fuel priorities weekly) but not their output or stockpiles.

### `cmdrec.py`

    python tools/cmdrec.py [--dir FOLDER] [--tag TAG]

Records every command the game executes (player and AI) while you play: turns on `bridge.cmdrec`, keeps stamping
the in-game date into the record until Ctrl+C, which turns recording off. Each new in-game day it also snapshots
every division (`bridge.snap`) into `unit_snapshots.tsv`: owner, province, order, organisation, strength,
template.

Battle plans: the record also holds the lists behind battle-plan commands (divisions, path points,
fortification provinces), and each in-game hour the country's lines are read from memory into `plans.tsv` when they
changed: army, cohesion, kind, line id, parent line, executing, provinces, border pairs, source (a naval invasion's port; its provinces are then the targets). `hoi4ai/planrec.py` has the
layouts and `decode()` for each battle-plan command. `--no-plans` turns it off.

Country state (`hoi4ai/countryrec.py`): each in-game day the country's whole save-format block (`bridge.country`:
production lines, stockpile, research, politics, focus, training queue, divisions) goes to `country/DAY.txt.gz`,
headline numbers (political power, manpower, factories, fuel, experience, stability, war support) to `country.tsv`,
and the equipment variants the block names by id to `equipment.tsv` (the template ids are in
`division_templates.tsv`). `intel.tsv` holds the country's intel (civilian, army, navy, air, 0-100) over every
other country each day: the player's fog of war, since what the map shows of a country's army follows army intel
(division counts "?" below 10%, then approximate; template details from 30%, exact from 80%). `--no-country` turns it
off.

Events (`hoi4ai/eventrec.py`): every event any country answers, by click or AI pick, news events included, goes to
`events.tsv` after each date mark: date, record line, the country answering, the country it came from (for a news
event, the one it is about), event id and name, option index and name, and how many options it has. The option
needs a hoi4-bridge with `bridge.cmdrec field` (it sits at +0xe8 of the command, past the bytes the record keeps).
Hidden events run no command and are not recorded. `tools/events.py RECORD [OUT]` decodes an older record
(names from the running game, same mods; no options).

Division templates (`hoi4ai/templaterec.py`): every country's templates go to `division_templates.tsv`, first all of
them (`start`), then each one `new`, `changed` (an edit keeps the template's id) or `removed`: date, change, id,
owner, name, obsolete (1 once decommissioned with the designer's button: the template stays, but can't be used for
new divisions), battalions, support companies. Battalions are the designer's grid, columns separated by `|`, each top to
bottom (`infantry,infantry,infantry|infantry,infantry,infantry|...`). The templates are read again whenever the record
holds a template command (create, edit in the designer, remove, set obsolete; those lines in `command_record.tsv` say
who acted), when a new template id turns up (checked each in-game day) and every 7 days, since focuses and events can
change templates without a command. A full read takes about a second. `--no-templates` turns it off.

Equipment designs (`hoi4ai/equipmentrec.py`): `equipment_designs.tsv` gets every country's designs at the start
(`start`, about 15 s), then each `new` design with the design it is based on, each design made `obsolete` (or
`unobsolete`): date, change, id, owner, name, type, modules, based on. A design is never edited: the designer saves a new one, and an "upgrade" keeps the old name (a
Bf 109 D with cannons is a new id based on the old Bf 109 D, which the player then usually makes obsolete). Existing
equipment isn't converted. New ids are probed each in-game day and after each design command. The obsolete flag
isn't readable from the design itself, so only changes made while recording show. `--no-designs` turns it off.

Production lines (`hoi4ai/productionrec.py`): `production_lines.tsv` gets every country's military production lines
at the start, then each line `new`, `changed` or `removed`: date, change, line id, owner (the design's owner, so a
licensed design shows the licensor), design id, design name, factories, priority (0 = top of the list),
manufacturer (military industrial organisation id, 0 = none), manufacturer name (its token,
`GER_messerschmitt_organization`, from the owner's save-format block). A change is adding or taking away factories,
switching the line to another design, moving it in the list (moving one line shifts the priorities of the lines
around it) or assigning, switching or removing the MIO. The lines are read (under a second) after each production command in the record (those
lines say who acted) and each in-game day, which catches the AI: it changes its lines without commands. Naval lines
aren't covered yet. `--no-production` turns it off.

MIOs (`hoi4ai/miorec.py`): `mios.tsv` gets every MIO of the country and of every other country with production
lines at the start (`start`, about 7 s), then each MIO `changed` (size, trait points or traits; funds are
written but grow daily, so they don't count), `new` or `removed`: date, change, country, MIO token, id (the
production lines' manufacturer), size, points, funds, traits (the unlocked trait tokens). Picking a trait runs no
command, so the state is read instead: the country's own MIOs each in-game day, the others every 7 days, from
the save-format block (`bridge.country`). A trait pick shows as one point fewer and one more trait. `--no-mios`
turns it off.

Market (`hoi4ai/marketrec.py`): `market.tsv` gets every purchase contract between any two countries and the
equipment up for sale, at the start and then as each is `new`, `changed` or `removed`: date, change, kind
(`contract` or `listing`), id (the contract's, or a listing's design id), seller, buyer, designs, names (the equipment
type for an unnamed base design), amounts, factories and cost (contracts; a contract read in the hour it is made has factories 0, and a `changed`
row with them follows the next day). Contracts are game objects (handle 81) read
each in-game day and after a market command; listings come from `market_stockpile` in the save-format block: the
country's each day, other producing countries' every 7 days. In the record, putting equipment up for sale, changing
the amount and taking it back are each a `CMarketStockpileEquipmentTransferCommand@NInternationalMarket` (the design
id and amount in the last column, `D+0x48` to the market and `D+0x88` back; needs a bridge with `deref` paths), and
buying is a `CDiplomaticActionCommand` with `P+0x28=CRequestEquipmentPurchaseAction@NInternationalMarket`.
`--no-market` turns it off.

Doctrines (`hoi4ai/doctrinerec.py`): `doctrines.tsv` gets every country's grand doctrine per folder and the
subdoctrine on each track, at the start and then as each is `new`, `changed` or `removed`: date, change, country,
folder (`land`, `naval`, `air`, `special_forces`), track (empty for the grand doctrine, else `infantry`, `armor`...),
doctrine and was (the game's keys, e.g. `superior_firepower`, `defensive_postures`) and the track's mastery. They are
read from memory (`CCountryDoctrineStatus@NDoctrines`; the save-format block has no doctrines) each in-game day and
after a doctrine command; finding the objects is a memory scan (~10 s) at the start and every 7 days. In the record,
picking is `CUnlockGrandDoctrineCommand@NDoctrines` (template pointer +0x28, country +0x30) and
`CUnlockSubDoctrineCommand@NDoctrines` (folder +0x28, subdoctrine +0x30, country +0x3c). `--no-doctrines` turns it off.

Officer corps spirits (`hoi4ai/spiritrec.py`): `spirits.tsv` gets the army, navy and air spirits of each country, at
its first read and then as each slot is filled (`new`), replaced (`changed`) or emptied (`removed`): date, change,
country, branch (`army`, `navy`, `air`), slot (`academy_spirit`, `army_spirit`, `division_command_spirit`,
`naval_academy_spirit`, ...), spirit and was (idea keys). Spirits are ideas, read from `ideas={...}` in the
save-format block (the slots and their ideas from `common/idea_tags` and `common/ideas`): the country's each day,
other producing countries' every 7 days, and a country's at once after an idea command for it. In the record, filling
a slot is `CAddIdeaCommand` (country +0x28, `CIdea*` +0x30, its name at +0x18) and replacing one is
`CReplaceIdeaCommand` (country +0x28, old +0x30, new +0x38). `--no-spirits` turns it off.

Logistics (`hoi4ai/logisticsrec.py`): `logistics.tsv` gets each country's fuel prioritization and each army's
motorization priority at its first read and then each change: date, change (`start`, `new`, `changed`, `removed`),
country, setting (`fuel_army`, `fuel_air`, `fuel_navy`, `motorization`), target (the army's orders group id and name,
e.g. `409 Army 1`), value (`low`, `medium`, `high`) and was. Read from the save-format block: `fuel_status`
(`fuel_consumer_data` index 0 army, 1 air, 2 navy; priority 0 low, 1 medium, 2 high) and each `orders_group`'s
`motorization_level` (0 low: no trucks, the horse icon; 1 medium: one truck; 2 high: several trucks); the country's
each day, other producing countries' every 7 days, and a country's at once after a logistics command for it:
`CSetFuelPriorityCommand` (country +0x28, index +0x2c, priority +0x30) or `CSetOrderGroupMotorizationCommand` (army
handle `53:ID` at +0x28, level byte +0x30). `--no-logistics` turns it off.

Training and deployment (`hoi4ai/deploymentrec.py`): `deployment.tsv` gets each country's deployment orders and the
divisions in training under them at its first read and then each change: date, change (`start`, `new`, `changed`,
`removed`, `deployed`), country, order (handle 64 id), line (handle 63 id; empty on an order row), template (id), name,
amount, location (province), priority (0 low, 1 normal, 2 high; orders only), training (a line's training, written but
not compared) and was (what changed, as `field=old`). `deployed` is a division leaving a line for the field: deployed
early (`CDeployConveyorLineCommand`) or done training; a line the player took away (`CRemoveConveyorLineCommand`) or
whose order was cancelled (`CRemoveConveyorCommand`) is `removed`. Read from the save-format block's
`deployment={ military_deployment_conveyor ... military_deployment_line ... }`: the country's each day, other producing
countries' every 7 days, the recorded country at once after `CCreateConveyorCommand` (template `52:ID` +0x28; the
command has no country) and a country at once after a command on one of its orders or lines (`CAddConveyorLineCommand`,
`CSetConveyorPriorityCommand`, `CSetConveyorLocationCommand`, `CChangeConveyorTemplateCommand`, `CRemoveConveyorCommand`:
order `64:ID` +0x28, value +0x30; `CRemoveConveyorLineCommand`, `CDeployConveyorLineCommand`: line `63:ID` +0x28).
`--no-deployment` turns it off.

Generals (`hoi4ai/generalrec.py`): `generals.tsv` gets each country's generals and field marshals at its first read and
then each change: date, change (`start`, `new`, `changed`, `removed`), country, leader (handle 4713 id), name, role
(`general`, `field_marshal`), attack, defense, planning, logistics (as the game shows them), traits (comma-separated, in
the game's order, passing ones like `recently_promoted` included), army (the army or army group it commands, e.g.
`409 Army 1`) and was (`field=old`, `;`-separated). Read from memory: the `CArmyLeader` objects (a scan at the first
read and every 7 days; key +0x20, name +0x40, country +0x120, field-marshal flag +0xde8, role +0xe7c, skills
+0xf58/+0xf68/+0xf78/+0xf88, traits a vector of `CUnitLeaderTrait*` at +0xdc8 with the key at +0x10 of each) and each
army's leader (`bridge.object 53 ID`, `CArmyLeader*` at +0x88). The country's each day, other producing countries'
every 7 days, and a country's at once after `CSetArmyLeaderCommand` (leader `4713:ID` +0x28, army `53:ID` +0x30),
`CPromoteUnitLeaderCommand`, `CLearnTraitCommand` (leader +0x28, trait +0x30), `CUpgradeDivisionOfficerCommand`
(recruiting: Officers, Promote; division `51:ID` +0x28; a new scan finds the new general) or `CCreateUnitLeaderCommand`.
`--no-generals` turns it off.

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
