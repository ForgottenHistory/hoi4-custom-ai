# Findings: how the game AI plays

Germany vs Poland, 1939 start. "Player" is a human playing Germany; "game AI" is the built-in AI playing it.
Recorded with `tools/battlerec.py` and `tools/cmdrec.py`.

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

That is what the planner in this repo copies: few plans, and direct moves to concentrate force. See
[planner.md](planner.md).
