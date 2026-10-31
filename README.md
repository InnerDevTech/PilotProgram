# Jarvis Desk

An interactive desk surface. A projector draws onto the desk, a grid of 16 infrared
beams tracks where your hand is, a camera overhead recognises marker-tagged objects,
and an Arduino handles four buttons and a knob.

Put a mug on the desk and a ring appears around it. Wave your hand through the beams
and a ball of light bounces away from it. Press a button and the desk shows a clock,
a timer or a stopwatch.

Built over 12 weeks as a FocusedAI internship project, in seven sprints.

---

## Status

| Sprint | Weeks | Focus | Status |
| --- | --- | --- | --- |
| [0 — Setup and plan](wiki/sprint-0.html) | 1 | Tools installed, plan made, parts ordered | In progress |
| [1 — Buttons and screen](wiki/sprint-1.html) | 2–3 | Buttons and knob drive a live projector screen | In progress |
| [2 — Hand sensor](wiki/sprint-2.html) | 4–5 | 16 IR beams show where a hand is | Not started |
| [3 — Camera and calibration](wiki/sprint-3.html) | 6–7 | Camera sees marker objects, lined up with the projection | Not started |
| [4 — Physics loop](wiki/sprint-4.html) | 8–9 | Balls bounce off hands and objects | Not started |
| [5 — Polish and proof](wiki/sprint-5.html) | 10–11 | Stable, documented, tested | Not started |
| [6 — Showcase](wiki/sprint-6.html) | 12 | Presentation and reflection | Not started |

Started 9 October 2026.

**Sprints 1 to 3 are the minimum viable product.** Sprint 4 is a stretch goal. If time
runs short, cut stretch goals first — never the sprint demo or the docs.

**Rule for the team:** if a sprint's demo does not pass, do not start the next sprint.
Fix the gap first.

---

## Repository layout

```
bridge/jarvis_bridge.py      Arduino -> browser bridge, and the web server
bridge/projector.py          cast to the projector, page full screen on it
bridge/cast_click.ps1        clicks the projector in the Windows Cast panel
jarvis_desk/jarvis_desk.ino  Uno sketch: 4 buttons, knob, buzzer
web/index.html               the page the projector shows
tests/test_bridge.py         18 tests, no hardware needed
tests/test_projector.py      16 tests, no projector needed
requirements.txt

wiki/index.html              wiki home, open this first
wiki/wiki.css                shared styling for every wiki page
wiki/sprint-0.html ... -6    one wiki page per sprint
index.html                   redirect to wiki/index.html, nothing else
```

Two files are named `index.html` and they are unrelated: `web/index.html` is the
live desk page the projector shows, `wiki/index.html` is the sprint wiki home.

The project also carries four small files that are easy to mistake for clutter:

| File | What it is for |
| --- | --- |
| `.gitignore` | Keeps `__pycache__/` and the kiosk browser profile out of the repo |
| `.gitattributes` | Normalises line endings — `.ino` as LF, `.ps1` as CRLF. Do not delete it: the wiki pages contain `–` and `·` and this is part of what keeps them intact |
| `.vscode/settings.json` | Pins the Live Server port to 5501 so everyone previews the wiki on the same URL |
| `LICENSE` | The project licence |

`__pycache__/` folders are Python's compiled bytecode cache. They are generated
every time the bridge or the tests run, are already ignored by git, and can be
deleted at any time — Python rebuilds them on the next run.

Arriving in later sprints: `hand_sensor/` (Sprint 2); `bridge/calibrate.py`,
`bridge/make_markers.py` and `objects.json` (Sprint 3); `web/physics.js` and
`tests/test_physics.js` (Sprint 4); and `docs/` for the Build Guide and the sprint
reviews.

---

## Running the desk

Needs **Python 3.10 or newer**. Run everything from the project root.

```bash
python -m pip install -r requirements.txt
python bridge/jarvis_bridge.py
```

That starts the bridge, casts to the projector and opens the desk page full screen
on it. The page is also at <http://localhost:8000> if you want it in a normal tab.
`Ctrl+C` stops the bridge and closes the full-screen window.

| Command | What it does |
| --- | --- |
| `python bridge/jarvis_bridge.py` | Find the Arduino, cast to the projector, show the page |
| `python bridge/jarvis_bridge.py --no-serial` | No hardware — drive the screens from the keyboard |
| `python bridge/jarvis_bridge.py --no-cast` | Skip the projector, plain browser tab on this screen |
| `python bridge/jarvis_bridge.py --list-ports` | Show which serial ports exist |
| `python bridge/jarvis_bridge.py --port COM3` | Skip auto-detection and use this port |
| `python bridge/jarvis_bridge.py --http-port 8080` | Serve on a different port |
| `python bridge/jarvis_bridge.py --no-browser` | Open nothing — no cast, no tab |
| `python bridge/jarvis_bridge.py -v` | Log every line coming off the Arduino |

`--desk` and `--camera` are accepted but do nothing yet — placeholders for Sprints 2
and 3. Check the top right of the page for "Arduino connected".

### The projector

The projector is a Miracast dongle — the one on this desk calls itself
`Hccast-5B8F5D_mira`. `bridge/projector.py` does three things at startup:

1. **Casts to it.** There is no command line for "connect to a wireless display", so
   it presses `Win+K` and clicks the dongle in the Cast panel through UI Automation.
   If the dongle is already connected, or plugged in over HDMI, this step is skipped.
2. **Extends.** The projector becomes a second screen, so the desk page gets it to
   itself and you keep the laptop panel for the terminal.
3. **Opens the page full screen on it** — Chrome or Edge in kiosk mode, positioned on
   the projector's screen. No tabs, no address bar.

| Command | What it does |
| --- | --- |
| `--list-cast-devices` | Print the wireless displays this PC knows, and every screen |
| `--cast-test` | Try the cast on its own and print what the Cast panel showed |
| `--cast-debug` | Same dump, but while the bridge runs normally |
| `--cast-device mira` | Match a different dongle by part of its name |
| `--cast-mode duplicate` | Mirror both screens instead of extending (`keep` leaves Windows alone) |
| `--cast-timeout 60` | Wait longer for the projector screen to appear |
| `--no-cast` | Do not cast at all |

The cast is best-effort: if clicking the dongle does not work, the bridge tells you to
press `Win+K` and pick it yourself, and keeps waiting for up to `--cast-timeout`
seconds. **It never stops the bridge** — the page is being served the whole time.

First run with a new dongle: pair it once by hand (`Win+K`, pick it). Windows
remembers it after that, and `--list-cast-devices` will show it.

### How it fits together

```
4 buttons + knob -> Arduino Uno -> USB serial -> jarvis_bridge.py -> web/index.html
```

The sketch prints lines like `BTN 2` and `KNOB 30`. The bridge reads them on a
background thread, pushes them to every open browser tab as Server-Sent Events, and
serves `web/` over HTTP on 127.0.0.1.

### Tests

```bash
python -m unittest discover -s tests -v   # 34 tests, ends with OK
```

No hardware needed, so run them after any change to the bridge.
`tests/test_physics.js` arrives with the physics loop in Sprint 4.

### Keys on the desk page

| Key | Does |
| --- | --- |
| `1` – `4` | Stand in for the four buttons: move between Home, Timer and Stopwatch, and speak the time |
| `C` | Recalibrate the hand sensor against the current room light (Sprint 2) |
| `b` | Add a ball (Sprint 4) |

---

## Hardware

| Part | Notes |
| --- | --- |
| Arduino Uno | 4 buttons on pins 2 to 5 (`INPUT_PULLUP`), knob on A0, buzzer on pin 8 |
| Arduino Mega | The 16 IR beams — 8 across X, 8 across Y |
| IR emitters and receivers | One 220 Ω and one 10 kΩ resistor per beam |
| Camera | Mounted above the desk, pointing down, whole projection rectangle in view |
| Projector | Focus locked, keystone correction **off** |
| 5 V supply | Powers the IR emitters, grounds joined to the Arduino |

A clear beam reads about 3 to 4.5 V and a blocked one under 0.5 V. Tune
`BLOCKED_BELOW` and `CLEAR_ABOVE` in the hand sensor sketch so a hand registers
reliably but room light does not.

---

## The sprint wiki

**Open [`wiki/index.html`](wiki/index.html) in a browser.** That is the home page, and
every sprint page links back to it and across to the others. Opening the `index.html`
at the project root works too — it just forwards you there.

Every sprint page has the same four sections:

1. **What I did** — the goal, the demo, the user stories, and a day-by-day table of
   steps with a "Done when" for each.
2. **Test cases** — each one something you can run and get a clear pass or fail from.
3. **Expected outcome** — what should work by the end, plus the demo checklist and the
   definition of done.
4. **What actually happened** — written *after* testing: what worked, what broke, what
   took longer than expected.

Sections 1 to 3 are filled in from the project plan. Section 4 is yours to write as
you go, and it is the part that matters most at the showcase.

### Keeping it current

- Tick a checklist item by adding `class="done"` to its `<li>`.
- Mark a step or test done by changing `class="status todo"` to `class="status done"`,
  and the cell text to `Done` or `Pass`.
- Update a sprint's status in **two** places: the `Status` field on the sprint page and
  the table on `wiki/index.html`.
- All styling lives in `wiki/wiki.css` — change it once and every page follows.
- Save these files as **UTF-8**. They contain `–` and `·`; an editor that saves as ANSI
  will turn those into `â€“` and `Â·` across every page.

---

## How each sprint runs

1. **Plan** (day 1, 30 min) — pick the tasks, put them in TO DO, agree the demo date.
2. **Build** (days 2–8) — stand-up at the start of each session, move cards as they
   progress, ask for help early.
3. **Demo** (day 9 or 10) — run the demo checklist. Failures become cards in the next
   sprint's TO DO.
4. **Reflect** (last session, 20 min) — what went well, what did not, what changes.

Write the review in `docs/sprint-reviews/S<number>.md`:

```
# Sprint S<number> review
Goal:
Demo result: pass / partial / fail
What went well:
What did not go well:
Changes for next sprint:
Time planned vs. used:
```

The board has five columns, plus a Risks column added in Sprint 0:

```
BACKLOG | TO DO (this sprint) | DOING | TESTING | DONE
```

---

## Safety

- Everything runs at low voltage (5 V and 3.3 V). Never wire the desk to mains.
- The IR emitters are invisible to the eye but are still light sources — do not stare
  into them at close range, and do not point them at anyone's face.
- The overhead camera sees whatever is on the desk. Say so before you record anything,
  and do not leave it recording unattended.
- Power the 16 IR emitters from the separate 5 V supply with grounds joined, or the
  Arduino will reset under load.

---

## Troubleshooting

| Problem | Cause and fix |
| --- | --- |
| `Could not use COM3` | The Arduino Serial Monitor is open and holding the port. Close it, then start the bridge. |
| Buttons register twice per press | Debounce. Raise `DEBOUNCE_MS` in the sketch (default 30 ms). |
| Hand dot jumps around with nobody at the desk | Sunlight or a desk lamp on the IR receivers. Shade them, dim the room, press `C` to recalibrate. |
| Rings land next to objects, not on them | The camera or projector moved. Mark their positions with tape and re-run calibration. |
| Calibration finds fewer than 24 dots | Lighting. Even it out and re-run before changing anything else. |
| Arduino resets when the beams switch on | The emitters are drawing from the Arduino. Use the 5 V supply and join the grounds. |
| Balls freeze during a demo | The browser pauses animation for hidden windows. Keep the desk page in front and full screen. |
| `No wireless display matching 'mira'` | The dongle has never been paired, or it is off. Switch it on, press `Win+K`, pick it once. |
| The cast panel opens but nothing is clicked | UI Automation missed the dongle. Click it yourself — the bridge is still waiting — then run `--cast-test` and read the dump it prints, or `%TEMP%\jarvis-cast-dump.txt`, to see what the panel actually called it. |
| The page opens full screen on the laptop, not the projector | Windows had not finished adding the second screen. `Ctrl+C` and start the bridge again, or raise `--cast-timeout`. |
| A full-screen page is stuck on the projector | Kiosk mode has no close button. `Alt+F4`, or `Ctrl+C` the bridge — it closes the window on the way out. |

---

## Done definition for the whole project

- Every sprint demo checklist has passed at least once.
- The Sprint 5 "new person" test passed — someone who did not build it got it running
  from the docs alone.
- Both test suites pass.
- The Build Guide, README and wiki match the code.
- The showcase has been given and the reflection is saved.
