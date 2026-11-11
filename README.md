# hoi4-custom-ai

An experiment: replace Hearts of Iron IV's army AI with our own, driven from Python through
[hoi4-bridge](https://github.com/ForgottenHistory/hoi4-bridge), a `version.dll` that takes console and
`bridge.*` commands over a named pipe.

- **Singleplayer only.** Windows, Steam version of HOI4 (tested on 1.19.4).
- Nothing about the map or the countries is hard-coded: map data is read from the game's own files (through the
  active mods) and live data from the running game's memory, so mods and other countries work.
- This is research code. It reads game memory at offsets found by reverse engineering, so a game patch can break
  it until the offsets are updated. Keep a backup of your saves.

> Unofficial; not affiliated with Paradox Interactive.

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

The planner on top of this (`hoi4ai/front.py`) is modelled on how a player runs an offensive: infantry holds the
border, a fast army breaks through along the cheapest route to the enemy's victory points. See
[docs/planner.md](docs/planner.md).

## Setup

1. Install hoi4-bridge into the game (see its README) and check `console_bridge.log` shows it loaded.
2. Clone this repo next to it, so the layout is

        hoi4-bridge/
        hoi4-custom-ai/

   or set `HOI4_BRIDGE_CLIENT` to the bridge's `client/` folder.
3. `pip install -r requirements.txt` (Python 3.10+).

## Quick start

Start a new game as an observer (or play another country), pause, then:

    python tools/controller.py --tag GER --target POL --organize

This takes Germany's army from the game AI, builds an army structure, and runs the army with our planner every
few in-game hours. `--dry-run --once` prints one plan without sending anything. Recordings go to
`recordings/` (not in git: tens of MB each).

To see what the planner sees without touching the game:

    python tools/heatmap.py --tag GER --target POL

## Tools

| script | does |
|---|---|
| `controller.py` | run a country's army with our planner |
| `takeover.py` | take a country's army from the game AI (or give it back) |
| `ai_only.py` | switch the game's AI off for every country except the ones named |
| `heatmap.py`, `oddsmap.py`, `planmap.py`, `defence.py` | draw what the planner sees (PNG; sends nothing) |
| `battlerec.py`, `compare_runs.py`, `runmap.py` | record wars and compare how different players/AIs fought them |
| `cmdrec.py`, `analyze_cmdrec.py`, `analyze_snaps.py` | record and analyse every command the game executes |
| `mapcheck.py`, `templates.py` | check the map data against the game; list a country's division templates |

Usage and options for each are in [docs/tools.md](docs/tools.md).

## Docs

- [docs/tools.md](docs/tools.md): every script in `tools/`
- [docs/planner.md](docs/planner.md): how the planner decides, and what each `hoi4ai/` module does
- [docs/findings.md](docs/findings.md): how the game AI plays compared with a player (Germany vs Poland, 1939)
- [docs/game-layouts.md](docs/game-layouts.md): memory layouts of the game objects we read (game 1.19.x)

## Layout

    hoi4ai/       the library: game files, map, memory reading, planner
    tools/        command-line scripts
    docs/         documentation
    recordings/   recorded games (git-ignored)
    .cache/       cached map data (git-ignored)

## License

MIT, see [LICENSE](LICENSE).
