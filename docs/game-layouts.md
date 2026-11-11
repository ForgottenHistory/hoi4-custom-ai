# Game object layouts (game 1.19.x)

Offsets of the fields we read from the running game, found by reverse engineering. They are specific to the
game version (1.19.4 as of 2026-11) and will move in a patch. `hoi4ai/memory.py` names objects by RTTI class,
which makes them easy to re-check; `bridge.object`, `bridge.mem` and `bridge.watch` in hoi4-bridge are the
tools for finding new ones.

- **Division** (`CArmy`, handle type 51; the handle's object is the full object + 0x10):
  - owner +0x1c8
  - province `*(+0x1e0)+0xa4`
  - main sub-unit type `*(+0x128)+0x68` (std::string)
  - destination +0x2a4 (0 = none)
  - probably supply level +0x490 (100000 = full; 72727 in a starving 23-division stack)
  - order `*(+0xb0)+0xc`
  - organisation +0x20
  - strength +0x30 (fixed point, 100000 = 100%)
  - template name `*(+0x3a8)+0x20`
  - full object: path at +0x2c0, destination +0x2b4
- **Unit leader** (`CArmyLeader`, handle type 4713):
  - name +0x40, key +0x20, country +0x120
  - type +0xe7c (1 usable; type 0 leaders get removed by the game when given a command)
  - field marshal flag +0xde8
  - skills: attack +0xf58, defence +0xf68, planning +0xf78, logistics +0xf88
- **Order** (front / battle plan / army, type 53): owner +0x3c.
- **Theatre** (type 67): no owner field; its armies' list at +0x80 (owner of the first), divisions' list at +0x68.
- **Provinces:** `CCurrentGameState` +0x2b0 is a vector of `CProvince*` indexed by province id.
  - `CProvince`: id +0xa4, `CState*` +0xc0, controller country id +0x188 (checked by changing a province's
    controller with `set_province_controller`), `CControllerArea*` +0xd0, `COwnerArea*` +0xd8.
  - `CState`: id +0x58 (agrees with the files for all land provinces), owner +0xc8, controller +0xcc.
- **`CMoveCommand`:** embeds a `CUnitMoveAction` at +0x28: division handle +0x38, province path (ids) +0x40 /
  count +0x4c. Player moves use flags `1 2 1 0 0 0 0`.
