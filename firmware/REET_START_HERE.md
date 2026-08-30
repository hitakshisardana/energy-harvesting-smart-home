# Hardware / firmware track — full brief for Reet

*Read this once top to bottom before starting. It is self-contained; the file
`firmware/README.md` is the terser technical reference you'll come back to.*

---

## 1. TL;DR

You own the **sensor node**: an ESP32-C3 running real firmware in **Wokwi** that
(a) looks at how much energy is in its local store, (b) picks a sensing **mode**
from that, (c) senses at the right rate, and (d) talks to the Python "home hub"
over **MQTT**. The firmware is already written — your first job is to get it
running, understand it, and prove it works. You can do 90% of this alone.

---

## 2. Where this fits in the project

The project is about spending **harvested energy** (footsteps, doors, exercise,
furniture compression) intelligently on smart-home sensing. There are two
simulators:

| | Python software sim (Member 3) | **Wokwi node (you)** |
|---|---|---|
| Scope | all 5 virtual nodes, months of data, all the ML | **one** node, real firmware, real time |
| Job | forecasting, scheduling, experiments, results | prove the node logic runs on hardware; the live demo |

They meet over MQTT: Python sends a schedule, your node obeys it and reports back.
That closed loop is a graded success criterion, which is why this track matters.

---

## 3. What the node must do (behaviour spec)

"Stored energy" is a value from **0 to 100 %**. In standalone mode it comes from a
potentiometer you turn; later the Python hub can supply it. From that value the
node picks a mode:

| Stored energy | Mode | Routine sensing |
|---|---|---|
| ≥ 70 % | `HIGH` | every 5 s |
| 40–69 % | `MEDIUM` | every 20 s |
| 15–39 % | `LOW` | every 60 s |
| < 15 % | `CRITICAL` | none — reserve energy for alarms only |

On every sensing tick the node: reads the DHT22 (temp + humidity), blinks the
LED, and **publishes its state** as JSON over MQTT. It also **subscribes** to a
"schedule" topic; when the hub sends a command, the node switches to the
commanded mode/interval and keeps it until the next command. The OLED always
shows energy %, mode, interval, and last event.

---

## 4. What you need

- A free account at **wokwi.com** (no software install — runs in the browser).
- Git, and access to the team GitHub repo (Hitakshi will add you as a
  collaborator).
- That's it. No Arduino IDE, no hardware, no MATLAB.

---

## 5. Get the code

```bash
git clone <REPO_URL>
cd energy-harvesting-smart-home
git checkout -b hw-node        # your own branch — never commit straight to main
```

Everything you need is in the `firmware/` folder:

| file | what it is |
|---|---|
| `REET_START_HERE.md` | this brief |
| `README.md` | technical reference + troubleshooting |
| `node.ino` | the firmware (already implements milestones M0–M3) |
| `diagram.json` | the Wokwi circuit |
| `libraries.txt` | libraries Wokwi auto-installs |
| `../config/interface.schema.json` | the message format — **shared with Member 3** |

---

## 6. Build it in Wokwi (step by step)

1. Go to **wokwi.com** → sign in → **New Project** → **ESP32** → pick
   **ESP32-C3**.
2. The new project has three tabs: `sketch.ino`, `diagram.json`, and a libraries
   area. Replace their contents:
   - `sketch.ino` ← paste all of `firmware/node.ino`
   - `diagram.json` ← paste all of `firmware/diagram.json`
   - Add a file named exactly `libraries.txt` ← paste `firmware/libraries.txt`
     (or add each library by hand in the Library Manager panel: `PubSubClient`,
     `ArduinoJson`, `DHT sensor library`, `Adafruit SSD1306`,
     `Adafruit GFX Library`, `Adafruit Unified Sensor`)
3. In `sketch.ino`, change **two lines** near the top:
   - `const char *PREFIX  = "ehsh-CHANGEME";` → a string unique to our team,
     e.g. `"ehsh-team7"`. (The MQTT broker is public and shared with the whole
     internet; a unique prefix keeps our messages separate.)
   - `const char *NODE_ID = "floor";` → leave as `floor` for now.
4. Press the green **▶ Play** button.
5. Open the **Serial Monitor** (bottom). You should see:
   ```
   wifi........ ok  192.168.x.x
   mqtt... ok
   subscribed ehsh-team7/floor/schedule
   -> {"node_id":"floor","ts":...,"stored_energy_pct":...,"mode":"...",...}
   ```

If it builds and prints that, **M0 is done**.

---

## 7. The circuit — what each part represents

| Wokwi part | Connected to | Stands for |
|---|---|---|
| Potentiometer | GPIO2 (analog) | **state of charge** of the supercapacitor, 0–100 % |
| PIR motion sensor | GPIO3 | someone moving past this node (occupancy / footstep) |
| Pushbutton (green) | GPIO4 | a manual event trigger — a footstep, or a door open/close |
| DHT22 | GPIO10 | the environmental-sensing task |
| LED (red) | GPIO5 | flashes each time the node performs a sensing task |
| SSD1306 OLED | GPIO8 / GPIO9 (I2C) | the node's little dashboard |

Turn the potentiometer knob in the running simulation and watch the mode and
interval change on the OLED and in the Serial Monitor. That is the core demo.

---

## 8. Your milestones (this is your first deliverable)

Do them in order. M0–M3 are **solo** and the code already does them — you are
verifying and understanding, not writing from scratch.

| # | Goal | You're done when… | Solo? |
|---|---|---|---|
| **M0** | Builds, boots, joins Wi-Fi | Serial shows an IP address | yes |
| **M1** | Mode follows the potentiometer | turning the pot moves the mode through CRITICAL → LOW → MEDIUM → HIGH and the interval changes on the OLED | yes |
| **M2** | Publishes state over MQTT | you can see the JSON arriving on topic `ehsh-team7/floor/state` in an MQTT viewer | yes |
| **M3** | Applies a schedule command | you publish a command by hand → the node switches mode/interval and the OLED shows `src: hub` | yes |
| **M4** | Closed loop with Python | Python receives state, sends back HIGH/MEDIUM/LOW, the node changes cadence | **with Member 3** |

---

## 9. Test MQTT without Python (for M2 and M3)

Use the **HiveMQ web client** — nothing to install:
<https://www.hivemq.com/demos/websocket-client/>

1. Host `broker.hivemq.com`, Port `8000`, click **Connect**.
2. Under **Subscriptions** add topic `ehsh-team7/#` → you'll see your node's
   `state` messages appear. **That proves M2.**
3. Under **Publish**, topic `ehsh-team7/floor/schedule`, message:
   ```json
   {"node_id":"floor","mode":"HIGH","sampling_interval_s":5,"schedule_ts":1}
   ```
   Click **Publish**. The Wokwi node should switch to HIGH / every 5 s and the
   OLED should show `src: hub`. **That proves M3.**

(Alternatives: the desktop app *MQTT Explorer*, or
`mosquitto_sub -h broker.hivemq.com -t 'ehsh-team7/#' -v` on the command line.)

---

## 10. The message contract (do not change alone)

Two message types. Full definition: `config/interface.schema.json`.

**State — node → hub**, topic `PREFIX/<node_id>/state`:
```json
{ "node_id": "floor", "ts": 91234, "stored_energy_pct": 63.2,
  "mode": "MEDIUM", "sampling_interval_s": 20, "last_event": "motion",
  "sensor": { "temp_c": 24.1, "rh": 48.0 }, "fw": "0.1" }
```

**Schedule — hub → node**, topic `PREFIX/<node_id>/schedule`:
```json
{ "node_id": "floor", "mode": "LOW", "sampling_interval_s": 60,
  "task_set": ["occupancy"], "schedule_ts": 91240 }
```

**Rule:** if you ever need to add or rename a field, edit
`config/interface.schema.json` in the *same commit* and message Member 3. The two
sides must never disagree on this file.

---

## 11. Git workflow

```bash
# once:
git checkout -b hw-node

# as you work:
git add firmware/
git commit -m "hw node: M1 mode state machine verified"
git push -u origin hw-node        # first push; later just: git push
```

When M0–M3 are done, open a **Pull Request** on GitHub from `hw-node` into
`main`. Don't merge your own PR without a teammate glancing at it.

**Pull other people's work regularly:**
```bash
git checkout main && git pull && git checkout hw-node && git merge main
```

---

## 12. What to hand back (definition of done for round 1)

- PR from `hw-node` with your Wokwi files.
- In the PR description: the `PREFIX` string you chose, and the topic names.
- A ~20-second screen recording showing **M3** — publishing a schedule command
  and the node visibly changing.
- One line confirming your `state` JSON matches `interface.schema.json`.

---

## 13. Things NOT to do

- **Don't look for a piezoelectric tile, a BQ25504 PMIC, or a supercapacitor part
  in Wokwi.** They don't exist there and you don't need them. Wokwi models the
  *firmware and behaviour*, not the analog power circuit. Energy is the
  potentiometer (later, a number from Python).
- **Don't switch from Wokwi to Simulink.** They do different jobs. Wokwi is
  required (graded). A Simulink/Simscape model of the harvester power circuit is
  an *optional bonus* later, only to get better energy-per-event numbers for the
  feasibility check — and only after checking with the team. It does **not**
  replace this track.
- **Don't commit to `main`.** Always your `hw-node` branch → PR.
- **Don't edit `config/interface.schema.json` without telling Member 3.**

---

## 14. Troubleshooting (top 6)

| Symptom | Fix |
|---|---|
| Nothing shows up in the MQTT viewer | You didn't change `PREFIX`, or you subscribed to the wrong topic. Subscribe to `#` briefly to see all traffic. |
| `mqtt rc=-2` repeating in Serial | Wi-Fi not up yet — wait ~10 s. If it persists, change `MQTT_HOST` to `test.mosquitto.org`. |
| DHT22 prints `nan` | Normal on the first read; code skips NaN. If it never works, check the DHT22 is on GPIO10 in the diagram. |
| OLED stays blank | Address mismatch — it's `0x3C` in both files. The node still works without it. |
| A library won't install | Add it manually via the Library Manager panel (names in step 6). |
| Compile error about `LOW` / `HIGH` | Don't rename the mode enum values — they're `M_LOW`, `M_HIGH` on purpose (Arduino already defines `LOW`/`HIGH`). |

---

## 15. Rough timeline

- **M0–M1:** ~2–3 hours.
- **M2–M3:** ~half a day (mostly learning the MQTT viewer).
- **M4:** a short joint session with Member 3 once their MQTT side is up.

If you're stuck more than ~30 minutes on one thing, message the group with the
Serial Monitor output pasted in.

---

## 16. Questions

- Firmware / Wokwi / circuit → ask in the group, paste Serial output.
- Message format / how the hub will use your data → Member 3.
- Scope / priorities / "should I also do X" → Hitakshi.
