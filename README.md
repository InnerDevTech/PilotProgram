# Jarvis Desk

An interactive desk surface. A projector draws onto the desk, an overhead webcam
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
| [2 — Hand sensor](wiki/sprint-2.html) | 4–5 | The webcam and MediaPipe show where a hand is | Not started |
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
bridge/hand_tracker.py       the hand sensor: webcam + MediaPipe (Sprint 2)
bridge/desk_geometry.py      camera pixels -> desk coordinates, and the steadying
bridge/calibrate_desk.py     click the four corners of the projection, once
bridge/models/               the MediaPipe model file lives here (not in git)
jarvis_desk/jarvis_desk.ino  Uno sketch: 4 buttons, knob, buzzer
web/index.html               the page the projector shows
calibration.json             written by calibrate_desk.py (not in git)
tests/test_bridge.py         18 tests, no hardware needed
tests/test_projector.py      16 tests, no projector needed
tests/test_desk_geometry.py  24 tests, no camera needed
tests/test_hand_tracker.py   15 tests, no camera and no model needed
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
| `python bridge/jarvis_bridge.py --camera 0` | Buttons **and** the hand dot (Sprint 2) |
| `python bridge/jarvis_bridge.py --smoothing 0.2` | Steadier hand dot, a little more lag |

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
| Webcam, overhead | The hand sensor — MediaPipe finds the hand in the picture |
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
- The overhead camera sees whatever is on the desk, and whoever is leaning over it.
  Say so before you record anything, and do not leave it running unattended.
- Hand tracking happens entirely on this machine. No picture and no video ever leaves
  it, and nothing is written to disk — only the hand position is sent to the page.
  Say that too, because people ask.

---

## The hand sensor (Sprint 2)

The desk knows where your hand is from the overhead webcam, not from any extra
hardware. MediaPipe finds the hand in each frame, three palm landmarks are averaged
into one point, and a homography turns that camera pixel into a **desk coordinate**:
two numbers from 0 to 1 across the projection, `(0,0)` at the top left. Every part of
the project that draws on the desk works in those numbers and never in camera pixels.

Set it up once, in this order:

```bash
python -m pip install -r requirements.txt
```

Then fetch the MediaPipe model, which pip does not install (about 7.8 MB, once):

```bash
python bridge/hand_tracker.py --where-is-the-model
```

That prints the one command that downloads it into `bridge/models/`. Finally, with the
projector on and showing the page, tell the camera where the projection is:

```bash
python bridge/calibrate_desk.py
```

Click the four corners of the projection **clockwise from the top left**, check the
white grid it draws sits square on the page, and press `s`. That writes
`calibration.json`. Now run the bridge with `--camera 0` and the dot follows your hand.

| Command | What it does |
| --- | --- |
| `python bridge/calibrate_desk.py` | Click the four corners, save `calibration.json` |
| `python bridge/calibrate_desk.py --check` | Print the saved calibration, no camera needed |
| `python bridge/hand_tracker.py --preview` | Camera window with your palm marked — the quickest "is it seeing me?" |
| `python bridge/hand_tracker.py --print` | Print desk coordinates, no window |
| `python bridge/hand_tracker.py --where-is-the-model` | How to fetch the model file |

The camera and the projector have to stay exactly where they were when you calibrated.
Nothing in the software can tell that they moved — the dot just lands in the wrong
place. Mark both with tape and re-run the calibration after any knock.

With no camera at all, press `h` on the desk page: the mouse stands in for a hand, so
the dot can be checked without any of this.

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
| `The desk is not calibrated yet` | Run `python bridge/calibrate_desk.py` with the projector on. |
| `MediaPipe's hand model is missing` | Run `python bridge/hand_tracker.py --where-is-the-model` and then the command it prints. |
| `Cannot open camera 0` | Something else has the webcam — Teams, Zoom, the Camera app. Close it, or try `--camera 1`. |
| The dot is there but in the wrong place | The camera or projector moved since calibration. Re-run `calibrate_desk.py`. |
| The dot lags behind your hand | `--smoothing` is too low. Raise it towards 1.0. |
| The dot shivers when your hand is still | `--smoothing` is too high. Lower it towards 0.05. |
| No dot, and the preview says "hand, but off the desk" | The camera sees your hand but the homography puts it outside the projection — usually a corner clicked in the wrong order. Re-calibrate clockwise from the top left. |

---

## Done definition for the whole project

- Every sprint demo checklist has passed at least once.
- The Sprint 5 "new person" test passed — someone who did not build it got it running
  from the docs alone.
- Both test suites pass.
- The Build Guide, README and wiki match the code.
- The showcase has been given and the reflection is saved.
