# hoi4-custom-ai

Custom army AI only: planner (`hoi4ai/`: front, plans, organize, defence, defend, battle, divisions, leaders, battlerec) and the
scripts that run or draw it (`tools/`: controller, takeover, ai_only, defence, templates, heatmap, oddsmap, planmap, mapcheck,
battlerec, compare_runs, runmap). Workspace notes are in `../CLAUDE.md`; docs in `docs/tools.md`, `docs/planner.md`, `docs/findings.md`.

Recorders, the report, analysis tools and the shared game-reading helpers (`memory`, `pdx`, `gamefiles`, `live`, `mapdata`,
`templates`, `wars`, `units`, `clock`, `drawing`) live in `../hoi4-recorder` (package `hoi4rec`); import them from there.
`tools/_bridge.py` adds it to `sys.path` (or set `HOI4_RECORDER`). Recorded games are in `../hoi4-recorder/recordings`; the
controller and battlerec may still create a fresh `recordings/` here. Grep the recorder before building a new recorder or analysis.

## Rules
- Commit and push straight to `main`; never branch. End commits with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Every army gets a battle plan with Immobile cohesion and the controller keeps moving troops; infantry is split evenly;
  supply counts as bad only below 50%.
- Edit code with the Edit/Write tools, not inline Python/sed heredocs.
