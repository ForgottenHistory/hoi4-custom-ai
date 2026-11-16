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

## Recording commands

What a recording holds, and what it doesn't:

- Decisions, as tables: the commands, events answered, division templates, equipment designs (modules, the design
  they're based on), production lines (design, factories, priority, MIO), MIO traits, market purchases and listings, doctrines, officer corps spirits, fuel prioritization, army motorization and exercises, generals, air wings (base, mission), task forces (mission, regions, engagement, repair) and admirals. A row says what was chosen,
  not what it does: a trait is its token, a design its modules.
- Results, as raw state: the recorded country's whole save-format block each day (`country/DAY.txt.gz`): output and
  efficiency of each production line, stockpile, MIO funds and size, research, manpower... Only a few headline
  numbers are pulled out (`country.tsv`); the rest has to be compared across days afterwards.
- Not recorded: what a choice means (a trait's bonus, a design's stats and cost). These follow from the game files
  (`common/military_industrial_organization`, equipment modules, localisation for the in-game names) and can be
  decoded afterwards. Other countries' state is also not kept: their decisions are logged (lines, designs,
  templates, market contracts, doctrines; MIOs, sale listings, spirits and fuel priorities weekly) but not their output or stockpiles.

### `cmdrec.py`

    python tools/cmdrec.py [--dir FOLDER] [--tag TAG] [--profile]

Records every command the game executes (player and AI) while you play: turns on `bridge.cmdrec`, keeps stamping
the in-game date into the record until Ctrl+C, which turns recording off. Each new in-game day it also snapshots
every division (`bridge.snap`) into `unit_snapshots.tsv`: owner, province, order, organisation, strength,
template. Two blocks a day: handle 51 (the divisions from the game's setup) and 4713 (divisions trained later).
By default the AI's own bookkeeping commands are not recorded (`SKIP` in cmdrec.py: section blocks, force
concentration, wanted division counts, pings; about half of a late game's record); `--skip` with no class records
It also copies the launcher's `dlc_load.json` (the active mods) into the folder, so the game files can be read
through the same mods later.
everything.

The game is never paused. The main loop only polls the date and writes the marks; the state logs below run on a
worker thread, one step at a time, each step covering every record line since the last one, so at speed 5 a step
can span a day or more. To keep steps short, a step reads each country's save-format block once
(`hoi4ai/shared.py`), the other producing countries are spread over the week (a seventh of them each day instead of
all of them every 7 days), and the memory scans for generals, task forces and doctrines run on one background
thread (`shared.objects`: one numpy pass over the game's memory for all classes, ~5 s, started when the logs are
made, rescanned every 30 s). `--profile` prints each step's time per log.

Dates: the game runs one `CHourlyTickCommand` per in-game hour and the record holds each one, so readers date a
command by counting the ticks from the marks (`hoi4ai/clock.py`). A mark's date can be some hours late when the
recorder is busy (mostly 1, up to 32 at speed 5), never early, so the latest anchor wins. `events.tsv` and
`analyze_cmdrec.py` date this way.

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
who acted; the AI's too, most steps at speed 5) or a new template id turns up (checked each in-game day); these read
the templates found before plus the ids past the highest. Every 7 days all templates are found again by id (about a
second), since focuses and events can change templates without a command. `--no-templates` turns it off.

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
after a doctrine command; finding the objects is a memory scan (~5 s) at the start and every 7 days. In the record,
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

Politics (`hoi4ai/politicsrec.py`): `politics.tsv` gets each country's national focus, laws, advisors and national
spirits at its first read (`start`) and then each change (`new`, `changed`, `removed`): date, change, country, kind,
slot, value, was. Kinds: `focus` (slot `current`: the focus being researched, removed when it ends; `paused`: `yes`
while paused; `completed`: each completed focus), `law` (slot the law category: `economy`, `trade_laws`,
`mobilization_laws`; `changed` with the old law in was), `advisor` (slot `political_advisor`, `theorist`,
`high_command`, `army_chief`, ...; value the character's token) and `idea` (other ideas the player can see, by
category: national spirits under `country`, manufacturers; hidden ideas and officer corps spirits are left out).
All of it comes from the save-format block: `focus={...}`, `ideas={...}` (laws are the ideas of the `common/ideas`
categories with `law = yes`) and `appointed_advisors` (slot and character id; the token read once with
`bridge.write 73 ID`). The country's each day, other producing countries' spread over 7 days, and a country's at
once after `CSetNationalFocusCommand`, `CDropCurrentNationalFocusCommand`, `CAddIdeaCommand` or `CReplaceIdeaCommand`
(country +0x28) or `CAddAdvisorCommand` / `CRemoveAdvisorCommand` (character `73:ID` +0x28; the country is the
character's). `--no-politics` turns it off.

Economy (`hoi4ai/economyrec.py`), two files. `construction.tsv` gets every country's construction lines, buildings
and railways, at the first read (`start`), then each line queued (`new`), changed (`changed`: the levels to build or
repair, or its place in the queue; was the old values) and ended (`done` when its last level was about finished,
else `removed`: cancelled or the state lost): date, change, country (the state's owner), line (the id; `rN` for a
railway), building (`rail_way` for a railway), state, province (province buildings: bunkers, naval bases, ...; a
railway's first and last province, `FIRST>LAST`), amount (levels left; 0 for a repair), priority (0 the top; railways
share the queue), repair, produced and cost (of the current level), factories (civilian factories on it), flags
(`conversion`, `allied`). A line finishing moves the lines below it up a place; only a change in their order counts.

`trade.tsv` gets every trade deal between any two countries, `start` / `new` / `changed` (amount or factories) /
`removed`: date, change, exporter, importer, resource, amount (per day), factories (the importer's civilian
factories paid), deal id, start date. Deals the automatic trading makes are deals like any other. The auto-import
settings come as rows with deal `setting`: exporter the country, resource the setting (`auto_import_enabled`,
`max_factories_for_auto_import`, `auto_import_prioritize_fuel`, `auto_import_fuel_topping`), amount its value.

Building lines are handle 59 (`CBuildingProductionLine`); railways (`CRailwayProductionLine`) and deals
(`CResourceExchange`) share handle 4713 with generals, convoys and more. Both are read with one batched
`bridge.write` of the ids seen before plus the next ones (new objects get higher ids), each new day and at once after
a construction or trade command (`CAddConstructionCommand`, `CRemoveConstructionCommand`,
`CChangeProductionLinePriorityCommand`, `CBuildRailwayCommand`, `CCreateTradeCommand`, `CSetAutoImport...`, ...):
about a second. The first read takes the lines and deals the memory scan finds (`shared.ids`) and walks up to the last id
(a few seconds; without a scan it probes every id, about 30 s). A building's location is a state id, or a province id
for buildings with `province_max` in `common/buildings`; owners come from memory (`hoi4ai.live.Provinces`). A deal's
text names only the importer; the exporter is found in memory (the deal's +0x80 is the exporter's
`CCountryResources`, at `CCountry` +0x11f8). Settings are read from the save-format block: the country each day,
other producing countries spread over 7 days, a country at once after its auto-import command (country id +0x28).
`--no-economy` turns it off.

Diplomacy (`hoi4ai/diplomacyrec.py`): `diplomacy.tsv` gets each country's relations at the first read (`start`), then
each one added (`new`), changed (`changed`, was the old value) and ended (`removed`): date, change, country, kind,
partner, key, value, was. Kinds: `pact` (every relation with a first and second country: guarantee, military_access,
puppet with its autonomy level, war_relation, improve_relation, lend_lease, ...; value
`FIRST>SECOND START`), `deal` (dated relations: trade, trade_equipment, truce_until), `opinion` (opinion modifiers, added and removed only), `wargoal`
(the type against the target; `justifying` while one is justified), `faction` (partner the leader, key the name; one
row per member), `incoming` (actions waiting for the country's answer) and `other` (the rest of the diplomacy block,
as one line: volunteer recall cooldowns, the faction join date, ...). Market access is left out (the game asks for
it by itself), and so is the date a diplomat was last sent (market access requests set it too). Read from the save-format block
(`diplomacy={ active_relations={ ... } wargoals={ ... } }`, `incoming_diplomatic_action={ ... }`): the country each
day, other producing countries spread over 7 days, the country at once after a diplomacy command
(`CDiplomaticActionCommand`, `CCreateFactionCommand`, `CReleaseCountryCommand`, ...; the actions hold their countries
behind pointers, so the other side is read on its day). Factions are not in a country's block: a `bridge.query` of
every country in a faction (`is_in_faction`, `is_faction_leader`, `[THIS.GetFactionName]`) each day and after a
diplomacy command. `--no-diplomacy` turns it off.

Occupation (`hoi4ai/occupationrec.py`): `occupation.tsv` gets each country's occupation settings at the first read
(`start`), then each one added, changed or removed: date, change, country, kind, target, key, value, was. Kinds:
`global` (`default_law`, the primary garrison `template` as `NAME #id`, the garrison `priority`), `country` (an
occupied country's own `occupation_law` and `template`), `state` (each occupied state's `law`, and a state's own
garrison settings) and `release` (`released` rows from the release command: target the released country, value the
panel's boxes, `puppet`, `play_as`, `retain_cores`, or `none`). Read from the save-format block
(`occupation_status={ default_law= division_template_id= occupation={ TAG={ occupation_law_list={...}
state_garrison_data={...} } } }`) for the country each day, other producing countries spread over 7 days, the
country at once after an occupation command (`CSetDefaultCountryOccupationPolicyCommand`,
`CSetOccupationPolicyCommand`, `CSetMainGarrisonTemplateCommand`, `CSetCountryGarrisonTemplateCommand`, ...).
Resistance, compliance and garrison strength are left out. `CReleaseCountryCommand`: +0x28 the releasing country id,
+0x2c the released, then one byte per box (+0x30 play as, +0x31 puppet, +0x32 retain cores; puppet is sure, the other two
likely). A released puppet also
shows in `diplomacy.tsv`. `--no-occupation` turns it off.

Decisions (`hoi4ai/decisionrec.py`): `decisions.tsv` gets date, change, country, decision, target, value, was. A
`taken` row for each decision the country takes (`CSelectDecisionCommand`,
`CSelectTargetedDecisionCommand`) and an `ignored` row for each one ignored (`CIgnoreDecisionCommand`,
`CIgnoreTargetedDecisionCommand`; decision `all` for `CIgnoreAllAvailableDecisionCommand`; `unignored` when it took
one off the ignore list), from the command: +0x28 the country id, +0x2c a targeted decision's target id, and a
pointer to the decision, a `CDecision` (name string at +0x120) or a `CTargetedDecision` (its `CDecision` at +0x10).
An ignore command's pointer is no decision, so it is named from what the read right after it finds added to (or
taken off) the ignore list. Only the country's own commands are written: the AI takes hundreds of decisions a day,
and the other countries' show in their status. Then the country's decision status at its first read
(`start`) and each change of it (`new`, `changed`, `removed`): running decisions and missions (`active N days`),
cooldowns (`re_enable_cooldown N days`), available targeted decisions (`available`, `available ignored`) and the
ignore list (`ignored`), from `decision_status={ active_timed_decision={...} active_targeted_decision={...} }` and
`player_settings={ ignore_decisions={...} }` in the save-format block; the days
left don't count as a change. Read for the country each day, other producing countries spread over 7 days, and the
country at once after a decision command. `--no-decisions` turns it off.

Intelligence (`hoi4ai/intelligencerec.py`): `intelligence.tsv` gets each country's intelligence agency at the first
read (`start`) and each part added, changed or removed: date, change, country, kind, key, value, was. Kinds: `agency`
(name, icon, `is_created`, defense, operative slots, the upgrade being built, ...), `upgrade` (each upgrade's level),
`operative` (an operative's mission and target, `MISSION>TARGET skill=N traits=...`), `recruitable` (the operatives
on offer, with their traits), `cryptology` (key the target country, `decryption=yes` while decrypting it) and `operation` (the operations priority; a running operation keyed `TYPE>TARGET`, value its auto commence,
completed, operative count and phases; `finished TYPE` with the targets and counts). From
`intelligence_agency={...}` and `operations={...}` in the save-format block; every country has an agency block,
also before it builds one. Progress that moves by itself (`building`, `upgrade_progress`, the next slot's days,
experience, trait progress, a mission's state, the amount decrypted) is left out. Read for the country each day, other producing countries
spread over 7 days, and the country at once after an intelligence command (`CIntelligenceAgencyCreationCommand`,
`CIntelligenceAgencyUpgradeCommand`, `CRecruitOperativeCommand`, `CSetOperativeMissionCommand`,
`CStartStopDecryptionCommand`, `CCreateOperationCommand`, ...). `--no-intelligence` turns it off.

Logistics (`hoi4ai/logisticsrec.py`): `logistics.tsv` gets each country's fuel prioritization and each army's
motorization priority and exercises at its first read and then each change: date, change (`start`, `new`, `changed`, `removed`),
country, setting (`fuel_army`, `fuel_air`, `fuel_navy`, `motorization`, `exercise`), target (the army's orders group id and name,
e.g. `409 Army 1`), value (`low`, `medium`, `high`; exercise `off`, `on`,
`until_max_xp`: started with a shift-click) and was. Read from the save-format block: `fuel_status`
(`fuel_consumer_data` index 0 army, 1 air, 2 navy; priority 0 low, 1 medium, 2 high) and each `orders_group`'s
`motorization_level` (0 low: no trucks, the horse icon; 1 medium: one truck; 2 high: several trucks) and
`training=yes` / `stop_training_at_max_xp=yes` (exercising; stopping at max experience); the country's
each day, other producing countries' every 7 days, and a country's at once after a logistics command for it:
`CSetFuelPriorityCommand` (country +0x28, index +0x2c, priority +0x30) or `CSetOrderGroupMotorizationCommand` (army
handle `53:ID` at +0x28, level byte +0x30) or `COrderSetTrainingCommand` (army `53:ID` +0x28, exercise byte
+0x30, stop at max experience byte +0x31). `--no-logistics` turns it off.

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

Air wings (`hoi4ai/airrec.py`): `air.tsv` gets each country's air wings at its first read and then each change: date,
change (`start`, `new`, `changed`, `removed`), country, wing (handle 69 id), name, base (air base, handle 65 id), state
(of the base), equipment (the plane design, handle 70 id), planes, mission (`none`, `air_superiority`, `cas`,
`interception`, `strategic_bombing`, `training`, ...: the bits of the game's mission mask), region (the mission's
strategic region), period (`day`, `night`, `day_and_night`), experience and was (`field=old`; planes and experience
aren't compared). Read with `bridge.write 69 ID` for every wing (the wing as the game saves it: `count`, `experience`,
`mission={ type period strategic_region }`, `tag`, `equipment`, `name`), the base from memory (the wing's
`CAirWingPool` at +0x28 of `bridge.object 69 ID`, the base handle `65:ID` at +0x18 of the pool) and its state with
`bridge.write 65 ID`. The country's each day, other producing countries' every 7 days, and the country's at once after
an air command on its wings: `CStratAirSetMissionCommand` (wings +0x28, mission mask +0x40, on +0x44),
`CStratAirEnableMissionCommand`, `CStratAirDayNightCommand` (wing `69:ID` +0x28, period +0x30),
`CStratAirTransferCommand` (wing +0x28, base `65:ID` +0x30), `CStratAirSplitCommand` (wing +0x28, planes +0x30),
`CStratAirConsolidateCommand` (merge), `CDeleteAirWingCommand` (disband) or `CDeployAirWingCommand` (base +0x30); the
wings of the listed ones are written to the record by `airrec.install_rules()` (`bridge.cmdrec list`). Needs a bridge
with `bridge.write`. `--no-air` turns it off.

Navies (`hoi4ai/navalrec.py`): `naval.tsv` gets each country's task forces at its first read and then each change:
date, change (`start`, `new`, `changed`, `removed`), country, task_force (`51:ID` for the starting ones, `61:ID` for ones made by splitting or balancing), name, fleet (handle 61 id and
name), ships (count per ship type, e.g. `destroyer:4,heavy_cruiser:2`), mission (`hold`, `patrol`, `strike_force`,
`convoy_raiding`, ..., `training`), regions (the fleet's mission regions), engagement (`do_not_engage`, `low_risk`,
`medium_risk`, `high_risk`, `always`), repair (`never`, `low`, `medium`, `high`), exercise (`until_max_xp` when
training stops at max experience), home_base (province), admiral (handle
4713 id and name), auto_reinforce and auto_split (`yes` when on), composition (the composition editor's requirements, e.g.
`submarine:0`, unit names from the save's `sub_units` index into common/units in file order), detached
(`reinforcing 61:682` for the ships detached from task force 682 to reinforce), location (not compared) and was (`field=old`). `admirals.tsv` gets each country's admirals: admiral
(handle 4713 id), name, skill, attack, defense, maneuvering, coordination, traits and the fleet they command. Read from
the country's save-format block (`fleet={ task_force={ mission={ mission navy_engagement_rule } repair_mode ship={
definition } auto_reinforcement repair_split repair_parent detached_activity
ai_taskforce_composition={ requirements={ sub_units amount } } } strategic_region home_base leader }`); admirals are found with a memory scan for `CNavyLeader` (country id
at +0x120) and read with `bridge.write 4713 ID`. The country's each day, other producing countries' every 7 days, and
the country's at once after the player's naval command (owner column `-`): `CNavalMissionSetTypeCommand` (task forces +0x28, mission +0x40, stop at
max experience +0x41), `CNavalMissionAddRegionCommand` (fleet `61:ID` +0x28, region +0x30), `CNavalMissionMoveCommand`,
`CSetNavyEngagementCommand` (rule +0x40), `CNavyRepairModeCommand`, `CReorganizeShipsCommand` (split),
`CMergeNaviesCommand`, `CSetFleetLeaderCommand` (admiral +0x28, fleet +0x30), `CSetFleetHomeBaseCommand` (fleet +0x28,
province +0x30), `CSetAsReserveFleetCommand`, `CLearnTraitCommand`, `CSetTaskForceAutoReinforcementCommand` (task
forces +0x28), `CSetTaskForceCompositionRequirementsCommand` or `CDeleteUnitCommand` (`61:ID` +0x28). `--no-naval` turns it off.

Research facilities (same module, same reads): `facilities.tsv` gets date, change (`start`, `new`, `changed`, `removed`), country, kind (`facility` or `project`), name (the building, e.g. `land_facility`, or the `sp_` special project), province, scientist (its id, empty when none), state (`id=N project=NAME dismantle=current/target`, plus `timed` while a dismantle runs, or `start=yes completed=no iterations=N` for projects shown once started; `kind=reward` rows are a project's history, each learning or choice taken: `option=… reward=… progress=… date=…`) and was. A dismantled facility ends as a `removed` row. Read from `program_status={ }` of the country block.

Research (`hoi4ai/researchrec.py`): `research.tsv` gets each country's research slots at its first read and then each
change: date, change (`start`, `new`, `changed`, `removed`, `done`), country, slot (1 the first), tech (empty: idle),
level, design_team (MIO handle 79 id and name, e.g. `23 GER_mauser_organization`), points (research points so far, not
compared) and was (`field=old`). A `done` row is a tech finished: its level, the design company that helped and, in
points, the game's own finish date (the row's date is when it was read). Read from the save-format block
(`technology={ technologies={ NAME={ level research_points date design_team } } slots={ NAME | empty } }`). The
country's each day, other producing countries' every 7 days, and the country's at once after its own
`CSetResearchCommand` (country id +0x28, `CTechnologyTemplate*` +0x30 with the name at +0x10, slot +0x38 from 0; every
country's show owner `-`) or a `CSetDesignTeamCommand` (design company `79:ID` +0x38). `--no-research` turns it off.

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

### `examples.py`

    python tools/examples.py RECORDING_DIR [--out FILE] [--tag GER]

Training examples from a cmdrec recording: `examples.jsonl`, one JSON object per decision of the player (the
record's `#mark player=`), with the state just before it. Decisions are the player's rows of the per-system logs
that are not `start` (politics, research, construction, production lines, trade, logistics, deployment,
diplomacy, naval, air, generals, events, ...), the battle plans of `plans.tsv` when an army's plan changes, and
the player's own commands of `command_record.tsv` (per in-game hour and class, dated by the hourly ticks; noise
classes left out), each decoded: moves (division, path, target province and state), rail moves, army assignments,
halts and battle-plan commands. The record has no owner for moves and rail moves of divisions trained during the
game (handle 4713) nor for halts; those are the player's when a division they name is the player's in
`unit_snapshots.tsv` (`"owner_from": "divisions"`). Divisions are keyed as in `analyze_snaps.py`: the id for
handle 51, `4713:ID` for later ones. `CMassMoveCommand` names no divisions and is left out. Province ids get
their state names from the game files through the mods in the recording's `dlc_load.json` (cmdrec saves a copy
of the launcher's at the start), else the mods active now. The state: the `country.tsv` row of the day before, the current focus, research slots, laws,
war goals, wars and puppets, replayed from the logs. `auto: true` marks rows the game makes by itself (a focus or
research finishing, construction done or repaired, deployments arriving, decisions becoming available, incoming
offers, other countries buying, deals made while auto import is on); they are kept as context. A decision taken
(`decisions`, `taken`) is the player's.

A log row is dated when the recorder read it, and the logs are read only every few in-game days (3-13 in the
Kaiserredux run, growing as the game got busier). Where the record holds the command behind a row, the row takes
the command's hour instead (`"logged"` keeps the log's date) and its state is from before that hour: focus picks
(`CSetNationalFocusCommand`), research (`CSetResearchCommand`), decisions taken, construction and railways, laws
and advisors (`CAddIdeaCommand`, `CReplaceIdeaCommand`) and trade deals. These commands hold their country's id at
+0x28 (the record has no owner for them); ids get their tags offline, from the record's event commands and the
tags `events.tsv` gives the same events. Each row takes the earliest unused matching command of the player
between the log's previous read (less a day) and the row. A row with its command is never `auto`; the commands
themselves are not separate examples.

### `playstyle.py`

    python tools/playstyle.py RECORDING_DIR [--out FILE]

How the player played, for a report page: `playstyle.json` in the recording folder (run `examples.py` first). It
holds the player's wars (the start is the game's own date of the war, from the `war_relation` value in
`diplomacy.tsv`), the `country.tsv` series (factories, manpower, political power, stability, war support,
states), per day the player's divisions with their mean strength and how many moved (`unit_snapshots.tsv`), per
month the player's army commands by kind (move, rail, halt, battle plan, organise, air, other) and the military
factories on each kind of equipment (replayed from `production_lines.tsv`), the focus picks with the day each
finished, laws and advisors, decisions taken, research picks, event choices, templates and construction the player
queued (dated as in `examples.jsonl`), and per war the army commands by kind in the 14 days before and after it
started with the other choices made in those 14 days.

### `apm.py`

    python tools/apm.py RECORDING_DIR [--gap-ms 400] [--out FILE] [--tag GER]

The player's actions per minute from a cmdrec recording: `apm.json` and a printed summary. The record's `ms` is real
time, so the minutes are real minutes whatever the game speed. The player's commands are found as `examples.py` finds
them; commands no more than `--gap-ms` apart count as one action (a click that sends one command per selected division
is one action, not a hundred). Gives APM over the whole span from the first to the last command, over the minutes with
any action, the median and 90th percentile of those, the busiest minute with its in-game date, and actions and commands
per class. `minutes` and `actions_list` in the JSON are the series. Commands the record holds no owner for (and that
name none of the player's divisions or country id) are missed, and what the game does for the player by itself is not
told from a click. Input level, off unless asked for: `cmdrec.py --input-apm` also counts key and mouse button presses
per second while HOI4 is the focused window (`input.tsv`, `hoi4ai/inputrec.py`), and `apm.py` then adds an `input`
block (presses per minute, keys against clicks). It keeps counts only, never which key or any text or pointer position,
ignores shift/ctrl/alt, counts nothing while another window is focused, and polls `GetAsyncKeyState` (no hook; Windows
only). Kaiserredux run: 2174 commands, 1411 actions, 9.1 APM (median 7, busiest minute 38). `input.tsv` seconds and the record's `ms` share one clock (`cmdrec` hands the input log the `GetTickCount64` tick at which it turned the record on), so `timeline` in `apm.json` holds each record minute's actions, keys and clicks together.

### `orbat.py`

    python tools/orbat.py RECORDING_DIR TAG [--json FILE] [--step 3]

The forces of one country (the AI's or the player's) as markdown tables, every `--step` months: the division
templates as they changed (`division_templates.tsv`), divisions per template (`unit_snapshots.tsv`), factories
on lines per kind of equipment (`production_lines.tsv`, as `playstyle.py` replays it) and the air force, wings and
planes per role and per aircraft model (`air.tsv`). A wing's equipment id is looked up in `equipment_designs.tsv`
and the role comes from its airframe (small_plane, ..._cas, medium, large, naval_bomber, `cv_` for carrier), not
the wing's name. A design missing from `equipment_designs.tsv` is counted under its id and role "other".
`--json` writes the same numbers.

### `campaignmap.py`

    python tools/campaignmap.py RECORDING_DIR [--save SAVE.hoi4 ...] [--tag NGR] [--pad 60] [--gif] [--out FILE]
                                [--no-open]

Maps of a recorded campaign, cropped on the player's land and wherever their divisions went:
`campaignmap_<TAG>.png` in the recording folder. The recording logs no state ownership, so the land at the start
comes from `history/states` (through the recording's `dlc_load.json` mods) and later land from each `--save` (plain
text saves: every state's owner, and each province's controller where the save names one). There is one panel per
date. It colours the player's starting land, land they gained, land they occupy without owning it, land they lost,
and every other country with its tag, and shows the player's divisions on that day from `unit_snapshots.tsv`. A last
panel shows the army's division-days per province over the whole recording, with the player's wars from
`diplomacy.tsv` (declared by them or on them, from the game's own date of each war). `--gif` also writes the army
every 7 days as an animation, with enemy divisions in red. Between saves the land is replayed day by day from
`unit_snapshots.tsv`:

- When an enemy capitulates (`capitulated_date` in `diplomacy.tsv`), all its land comes under the player's control.
- While the player is at war, each province their divisions pass through becomes theirs to control. The path is
  the shortest land path from yesterday's province to today's.
- Enemy divisions take back the provinces they pass through in the same way.
- When a war ends, the occupied land that is the player's in the next save becomes theirs, with the rest of those
  states. The rest of the occupied land goes back to its owner.
- Outside a war, a state that is the player's in the next save becomes theirs when their divisions first enter it.
- A state they lose goes at the last fall of their `country.tsv` state count before the next save.

The tool prints how far the replay is from that count. Encirclements and land a peace deal hands over without
troops are guesses, so save more often for better land.

## Reading the recordings

Read a recording with `rows.py DIR FILE [col=value ...]` (`--schema` prints the file's columns and the format its
recorder documents; `--count COLUMN` groups). It filters by column name, so a tag is never matched in the wrong
column: in `diplomacy.tsv` a faction row is `country=HUN kind=faction partner=GER` (HUN is the member, GER the
leader), and a grep for `GER` finds HUN joining the Axis as if Germany had. Look up what a column holds for each
`kind` with `--schema` before reading a row.

After a reload of an older save (`reloads.py DIR` lists them from the record's marks and writes `reloads.json`), every
file holds the repeated stretch twice; add `--final` to `rows.py` to keep only the replay (country.tsv 887 rows → 879
in the vanilla Germany recording). `focus_timeline.py DIR` rebuilds the player's focus timeline from the daily country
blocks into `focus_rebuilt.tsv` (politics.tsv's columns, dated by the day), for recordings made before the focus read
was fixed.

What to remember about the rows:
- **Dates are when the row was read**, not when the thing happened: the logs read on a thread, a step takes longer than
  a day at speed 5, and a country other than the player's is read weekly. Treat a date as "by this day".
- **A reload repeats dates.** Loading an older save puts a `date=?` mark and then an earlier date in the record, and
  everything from the load point appears twice (the abandoned timeline, then the replay); the `country/DAY.txt.gz`
  files of the repeated days are overwritten. The record's clock (`hoi4ai/clock.py`) follows a mark more than 72 hours
  back, so command dates after the load are the replay's; before that fix every date after a reload ran ahead by the
  length of the rewind (a 14-day rewind put the end at 4 September instead of 22 August). Find the repeats from the
  `#mark` lines in `command_record.tsv` going back.
- **`start` rows** are the first read of a country, not something that began that day.
- **`politics.tsv` recorded before `focus_body` (hoi4ai/politicsrec.py) lost the focus whenever a focus was
  highlighted in the UI** (the block gets a nested `activate_shine_on_focus={}` and the old pattern stopped at its
  `}`): every completed focus showed `removed`, then all `new` again in a batch (9 on 20 November 1936, back on
  9 April 1937). The vanilla_ger_1936 recording has this; rebuild a focus timeline from `country/DAY.txt.gz` instead.
- **An empty state is not a zero**: a log that only writes on change (plans, equipment, spirits) is quiet for months.
- **Counts that look like a cap** (experience at 500) are not checked against the game's rules: look in the game files.
- `facilities.tsv` holds every country's facilities, projects and rewards, not only the player's.

## Checking the data

### `audit.py`

    python tools/audit.py RECORDING_DIR [--tag NGR] [--json FILE]

Run it on every new recording, after `examples.py`, before analysing the data. One pass over the folder (a few
minutes for a 600 MB run) reports: the record's in-game span, real seconds per in-game day by quarter, save
reloads (the date going back), the player's commands by class, the commands with no owner and how many
`unit_snapshots.tsv` credits to the player or another country; the snapshots' missing days, the player's
divisions over time and sudden drops; per log the rows (all, the player's), first and last date, months with no
row of the player, and how often it was read (median hours between reads per quarter); `country.tsv`'s cadence,
gaps and columns that never change, and whether its `country/DAY.txt.gz` dumps read; the examples per source and
quarter, and the log rows dated by their command with how far they moved. Problems end the report as `WARN` lines;
`--json` writes all the numbers (for a report page).

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
