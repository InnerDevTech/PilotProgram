# Jarvis Desk control panel — build dimensions

Everything needed to rebuild the case in Onshape. All values in millimetres.

Hole sizes marked **confirmed** were proved on printed fit coupons, not calculated.

## Coordinate frame

Origin at the **outer front-left-bottom corner of the base**. X to the right,
Y toward the back, Z up.

| | |
| --- | --- |
| Base outer | 135 x 105 x 29.5 |
| Wall thickness | 2.5 |
| Floor thickness | 2.5 |
| Cavity | 130 x 100 x 27, from (2.5, 2.5, 2.5) |
| Panel | 135 x 105 x 3.0, sits z 29.5 to 32.5 |
| Overall assembled | 135 x 105 x 32.5 |

The 27 mm internal height comes from the buttons: they reach 22 mm below the panel
at their pin tips, plus about 5 mm for solder joints and for wires to turn.

## Holes in the top panel

Positions are hole centres on the panel's top face.

| Feature | Size | Centre (x, y) |
| --- | --- | --- |
| Button 1 | dia 7.0 **confirmed** | 32.5, 27.5 |
| Button 2 | dia 7.0 | 52.5, 27.5 |
| Button 3 | dia 7.0 | 72.5, 27.5 |
| Button 4 | dia 7.0 | 92.5, 27.5 |
| Pot bushing | dia 7.1 **confirmed** | 114.5, 27.5 |
| Anti-rotation tab slot | stadium, 4.5 across x by 3.0 in y | 114.5, 34.5 |
| LED "ON" | dia 4.9 | 102.5, 62.5 |
| LED "LINK" | dia 4.9 | 114.5, 62.5 |
| Buzzer vent, centre | dia 2.0 | 108.5, 87.5 |
| Buzzer vents, ring of 6 | dia 2.0 | radius 4.5 about the centre vent, every 60 deg |

Notes:

- Buttons sit on 20 mm centres. Their caps are small, so the spacing is set by
  fingertips, not by bezels.
- The tab slot is a stadium: two dia 3.0 circles at x = 113.75 and x = 115.25,
  y = 34.5, bridged by a 1.5 x 3.0 rectangle. It sits 7.0 mm from the pot centre and
  leaves a 1.95 mm web to the bushing hole — do not let it break into the hole.
- The tab slot points toward the **back** of the case, so the pot's pins face the
  front. The tab's angle relative to the pins is unconfirmed; the case is 4 mm wider
  than strictly needed so the pins clear the right wall whichever way they end up.
- LED holes are a press fit with no clearance. Nothing holds those LEDs but friction.

## Openings in the base

| Feature | Size | Position |
| --- | --- | --- |
| USB-B opening | 20 wide (y) x 16 tall (z) | left wall, y 42.5 to 62.5, z 7 to 23 |
| Vent slot x3 | 30 wide (x) x 3 tall (z) | back wall, x 47.5 to 77.5, centres at z 12, 17, 22 |

## Arduino Uno mounting

Board is 68.6 x 53.4, long axis along x, USB jack facing the left wall. Board's
front-left corner at (12.5, 42.5).

Four standoffs, **dia 6.0, 5.0 tall**, each with a **dia 2.8** pilot hole for an M3
self-tapper, sunk 1.5 into the floor:

| Standoff | Centre (x, y) |
| --- | --- |
| 1 | 26.47, 45.04 |
| 2 | 27.74, 93.30 |
| 3 | 78.54, 50.12 |
| 4 | 78.54, 78.06 |

Cable routing rib: 2.0 thick x 6.0 tall, x 7.5 to 9.5, y 62.5 to 97.5.

## Lid retention

The panel carries a lip that drops into the cavity with 0.3 clearance per side.

| Feature | Size | Position |
| --- | --- | --- |
| Lip | outer 129.4 x 99.4, wall 1.5 thick, 4.0 tall | z 25.5 to 29.5 |
| Lead-in ring under it | outer 128.4 x 98.4, wall 1.0 thick, 1.0 tall | z 24.5 to 25.5 |
| Snap bead on lip | 0.6 proud, 1.2 tall, 104 long | front and back lip faces, centred z 27.5 |
| Matching groove in base | 0.6 deep, 1.6 tall | front and back cavity walls, centred z 27.5 |

The bead is a small undercut, not a hook — it bridges fine on an A1 mini and comes
apart by hand. Keep the lip's outer face a plain vertical wall with a lead-in; a
hooked profile would need supports.

Buzzer retaining ring on the panel underside: OD 16, ID 12, 6.0 tall, at
(108.5, 87.5), z 23.5 to 29.5.

## Engraving

0.5 deep, 4 mm minimum cap height, strokes at least 0.8 wide so a 0.4 nozzle
resolves them in two passes.

| Text | Position |
| --- | --- |
| "1" "2" "3" "4" | above each button, y 37.5 |
| "1 SCREEN      2 START/PAUSE" | centred x 62.5, y 15.5 |
| "3 RESET       4 SAY TIME" | centred x 62.5, y 7.5 |
| "1-60 min" | centred x 114.5, y 40.5 |
| "ON" | centred x 102.5, y 70.5 |
| "LINK" | centred x 114.5, y 70.5 |
| "JARVIS DESK" | centred x 46.5, y 87.5 |

## Parts these dimensions came from

| Part | Key figures |
| --- | --- |
| Mini push button, threaded | thread 6.9, cap 6.0, thread length 6.0, 15.5 behind panel, 22.0 to pin tips |
| WH148 B10K pot | M7 bushing, 6.5 long; 6.0 shaft, 15.0 from body face; body dia 17, can 7.5 tall; 25.0 total over the pins; rectangular anti-rotation tab |
| Knob | dia 14.5, 15.0 tall |
| Active buzzer | dia 11.5, 9.0 tall |
| LEDs | 5 mm, bare, long legs; allow 15 clear behind each for an inline resistor |
| Arduino Uno R3 | 68.6 x 53.4, about 15 tall with headers |

## Print notes

Bambu Lab A1 mini, 180 x 180 bed, 0.4 nozzle, PLA. Base prints cavity up, panel
prints slab down with the lip pointing up. Neither needs supports. Base is 82 g,
panel 56 g.
