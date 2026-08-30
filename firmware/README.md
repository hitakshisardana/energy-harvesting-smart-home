# Hardware track - instructions (Member 1)

Owner: Reet. This is the **embedded node + Wokwi + MQTT** side of the project
(Phases 2, 3, and the hardware half of Phase 8). You can work almost entirely in
parallel with the Python side; the only shared thing is the message format in
`../config/interface.schema.json`, which must not change without telling Member 3.

---

## 1. What this track is - and is NOT

**IS:**
- an ESP32-C3 running real firmware that reacts to a stored-energy level by
  changing its **sensing mode** (HIGH / MEDIUM / LOW / CRITICAL)
- publishing its state over **MQTT** and accepting **schedule commands** back
- a visible demo: OLED shows energy %, mode, interval; LED blinks on each sense

**IS NOT:**
- a physical model of the piezo tile, the PMIC, or the supercapacitor.
  Those parts **do not exist in Wokwi and you should not look for them.**
  "Stored energy" is just a number - a potentiometer you turn, or a value the
  Python hub sends. The real Joule figures come from the Python energy model
  (and, in Phase 9, from bench measurement).

### "Should I use Simulink instead?"

No - not as a replacement. Wokwi and Simulink do different jobs:

| | Wokwi (this track) | Simulink + Simscape Electrical |
|---|---|---|
| Runs ESP32 firmware | yes | no |
| MQTT closed loop with Python | yes | no (awkward) |
| Models piezo -> rectifier -> PMIC -> supercap analog dynamics | no | yes |

The project's success criteria (plan Section 35) require *"a Wokwi ESP32 node that
changes behavior based on scheduler commands"* and *"a live or replayable Wokwi
<-> Python communication loop"*. That is this track. **Keep Wokwi.**

If you *also* want to build a Simulink/Simscape model of the harvester power
chain to produce better energy-per-event numbers, that is a useful **bonus** that
feeds `../config/energy.yaml` - but it is optional and must not replace the Wokwi
work or eat the whole schedule. Talk to the team before spending days on it.

---

## 2. Setup (Wokwi web editor - no install)

1. Make a free account at <https://wokwi.com>.
2. **New Project -> ESP32 -> ESP32-C3**.
3. In the project, replace the three files with the ones in this folder:
   - `diagram.json`  -> paste over the project's `diagram.json` tab
   - `node.ino`      -> paste over `sketch.ino` (rename tab to `node.ino` if you like)
   - `libraries.txt` -> add a new file with this exact name; Wokwi installs the libs
4. In `node.ino`, change **two** constants near the top:
   - `PREFIX`  -> something unique to the team, e.g. `ehsh-reet-4821`
     (the public broker is shared with the whole world - a unique prefix keeps
     your messages separate)
   - `NODE_ID` -> `floor` for now (later you can flash `door`, `furniture`, etc.)
5. Press the green **play** button. The Serial Monitor should show
   `wifi ok ... mqtt ok ... subscribed`.

If a library fails to resolve, open the **Library Manager** (bottom panel) and
add: `PubSubClient`, `ArduinoJson`, `DHT sensor library`, `Adafruit SSD1306`,
`Adafruit GFX Library`, `Adafruit Unified Sensor`.

---

## 3. The circuit (what each part stands for)

| Part (Wokwi) | Pin | Represents |
|---|---|---|
| Potentiometer | GPIO2 (ADC) | **state of charge** of the local supercapacitor, 0-100 % |
| PIR motion sensor | GPIO3 | an occupancy / footstep event at this node |
| Pushbutton | GPIO4 | a manual event trigger (footstep, or a door open/close on the `door` node) |
| DHT22 | GPIO10 | the environmental-sensing task (temp + humidity) |
| LED | GPIO5 | lights briefly every time the node performs a sensing task |
| SSD1306 OLED | GPIO8/9 (I2C) | the node dashboard: energy %, mode, interval, last event |

Turn the pot down and watch the mode step CRITICAL -> LOW -> MEDIUM -> HIGH and
the sensing interval change. That is the core behaviour to demo.

---

## 4. The message contract (shared with Member 3)

Full schema: `../config/interface.schema.json`. Topics:

```
<PREFIX>/<NODE_ID>/state       node  -> hub   (published every interval + on events)
<PREFIX>/<NODE_ID>/schedule    hub   -> node  (node applies it until the next one)
```

**State (node -> hub):**
```json
{ "node_id": "floor", "ts": 91234, "stored_energy_pct": 63.2,
  "mode": "MEDIUM", "sampling_interval_s": 20, "last_event": "motion",
  "sensor": { "temp_c": 24.1, "rh": 48.0 }, "fw": "0.1" }
```

**Schedule (hub -> node):**
```json
{ "node_id": "floor", "mode": "LOW", "sampling_interval_s": 60,
  "task_set": ["occupancy"], "schedule_ts": 91240 }
```
Optional field `stored_energy_pct` in a schedule message makes the node use the
hub's energy value instead of its own pot - this is how the Python sim will drive
node energy during a full closed-loop run.

**Rule:** if you need to add or rename a field, change
`interface.schema.json` in the same commit and tell Member 3. Do not diverge.

---

## 5. Milestones (do them in order)

| # | Goal | Done when... |
|---|---|---|
| **M0** | Project builds, board boots, joins Wokwi-GUEST Wi-Fi | Serial shows `wifi ok` + an IP |
| **M1** | Mode state machine from the pot | turning the pot moves mode through CRITICAL/LOW/MEDIUM/HIGH; OLED updates |
| **M2** | Publish node state over MQTT | you can see JSON arriving on `<PREFIX>/floor/state` in an MQTT viewer (see below) |
| **M3** | Apply a schedule command | publish a schedule JSON by hand; the node switches mode/interval and the OLED shows `src: hub` |
| **M4** | Closed loop with Python (with Member 3) | Python receives state, sends back HIGH/MEDIUM/LOW, ESP32 changes cadence - this is the **Phase 3** milestone |

`node.ino` as shipped already implements M0-M3. Your job first is to get it
running, understand it, and prove M2/M3 with an external MQTT client.

---

## 6. Testing MQTT without Python

Use any one of these:

- **HiveMQ web client** (nothing to install):
  <https://www.hivemq.com/demos/websocket-client/> -> connect to
  `broker.hivemq.com` port `8000` (websockets) ->
  Subscribe to `<PREFIX>/#` to watch all node traffic ->
  Publish to `<PREFIX>/floor/schedule` with a schedule JSON to test M3.
- **MQTT Explorer** (desktop app): connect to `broker.hivemq.com:1883`.
- **CLI**: `mosquitto_sub -h broker.hivemq.com -t '<PREFIX>/#' -v`

Test schedule payload to publish for M3:
```json
{"node_id":"floor","mode":"HIGH","sampling_interval_s":5,"schedule_ts":1}
```

---

## 7. Common pitfalls

- **Nothing on the broker:** you forgot to change `PREFIX`, or you're subscribed
  to the wrong topic. Subscribe to `#` briefly to see everything.
- **`mqtt rc=-2` forever:** Wi-Fi not up yet, or the broker is briefly down - the
  code retries; give it 10 s. Try `test.mosquitto.org` as a fallback host.
- **DHT reads `nan`:** first read after boot is often NaN; the code already skips
  NaN. If it never works, check the DHT22 is on GPIO10.
- **OLED blank:** address is `0x3C` in `diagram.json` and the sketch; some parts
  use `0x3D`. The node still works without the OLED (`haveOled` guards it).
- **`LOW` / `HIGH` name clashes:** they are Arduino macros - that's why the mode
  enum is `M_LOW`, `M_HIGH`, etc. Don't rename them back.
- **ESP32-C3 ADC** is non-linear near the rails; the pot won't hit exactly 0 or
  100 %. Fine for this - thresholds have margin.

---

## 8. What to hand to Member 3

- the `PREFIX` string you chose
- confirmation that `state` messages match `interface.schema.json`
- the topic names
- a short screen recording of M3 (schedule command changing the node) for the
  integration checkpoint

---

## 9. Later (Phase 8 / physical)

- Flash the same firmware to a real ESP32-C3; replace the pot with the real
  supercapacitor voltage through a divider into the ADC.
- The mode thresholds move from "% of pot" to real volts (e.g. 3.3 V min
  regulator input -> 0 %). Keep the same state-machine code.
- Deep sleep on real hardware drops Wi-Fi; for the demo, "CRITICAL" just stops
  routine sensing (as coded). Note this difference in the report (plan Section 33).
