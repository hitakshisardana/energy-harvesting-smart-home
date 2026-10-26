# Energy-Harvesting Smart Home: Feasibility Study

A battery-free smart-home sensor node that runs only on energy harvested from
people moving around the home. This repository holds the trace-driven simulator,
the energy model, the data pipeline, the forecasting experiments, the feasibility
sweep, the hardware firmware and hub, and the results.

TRL 3 research prototype. Three-person capstone team. The full design is in
`Energy_Harvesting_Smart_Home_Full_Project_Plan.docx`.

> **Read this first.** Almost every energy number here is a placeholder or an
> assumption. Only the event rates and the footstep energy (derived from body
> mass) come from data. Nothing has been measured on hardware. Read results as
> relative comparisons under a stated parameter set, never as absolute claims.

---

## Contents

1. [The question](#1-the-question)
2. [Glossary](#2-glossary)
3. [The system in one picture](#3-the-system-in-one-picture)
4. [Team and ownership](#4-team-and-ownership)
5. [Data](#5-data)
6. [The energy model](#6-the-energy-model)
7. [Schedulers and forecasters](#7-schedulers-and-forecasters)
8. [Metrics](#8-metrics)
9. [Results so far](#9-results-so-far)
10. [What the project does not yet do](#10-what-the-project-does-not-yet-do)
11. [Hardware, firmware and the hub](#11-hardware-firmware-and-the-hub)
12. [Reproducing everything](#12-reproducing-everything)
13. [Repository layout](#13-repository-layout)
14. [Decisions still open](#14-decisions-still-open)
15. [Conventions](#15-conventions)
16. [History and superseded material](#16-history-and-superseded-material)

---

## 1. The question

Given the same limited, intermittent harvested-energy budget, can predictive
forecasting and coordinated scheduling let a node sense more usefully than fixed
or reactive energy-neutral operation?

**Where the answer stands.** The plan assumed "yes, prediction helps". The
experiments say:

- **Holding energy back for quiet periods matters a lot.** This needs no forecast.
- **Better prediction adds almost nothing** on this data. Even a perfect forecast
  adds at most about 0.002 to the capture metric, inside the seed noise.
- **Whether a node can run at all depends on how much energy one footstep gives.**
  At the gait-derived value, the floor and ambient nodes run. The furniture and
  door nodes stay below their standing overhead under kinetic-only harvest.
- The project is now framed as a **feasibility map**: under what parameter regime
  can kinetic harvesting fund a sensing node, and above that boundary, how much
  does the scheduling policy matter?

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Kinetic source** | Energy from human movement. Four of them: footsteps, furniture compression, door flybys, exercise. |
| **Footstep** | One foot strike on the floor tile. Gives piezo energy. |
| **Visit** | A run of motion events in one room or node, separated by more than 60 s. The unit the footsteps-per-visit factor multiplies. |
| **Footsteps per visit** | How many steps one visit represents. A swept parameter (5 to 80). CASAS cannot measure it. |
| **Store** | The supercapacitor: 0.047 F, 440.6 mJ usable. |
| **Standing overhead** | Energy lost every day whether or not the node senses: leakage plus MCU sleep, about 717 mJ/day. |
| **Mode** | HIGH, MEDIUM, LOW or CRITICAL, picked from the store level. Sets how often the node senses. |
| **Sensing round** | One reading (occupancy and environment) plus one radio packet. About 14.1 mJ. |
| **Outage** | Fraction of time a node cannot pay for its standing overhead, or cannot run. |
| **Capture** | Fraction of injected critical events the node senses and reports in time, measured during silence. |
| **Silence** | A stretch where a node's harvest is below its standing overhead. |
| **Oracle** | A forecaster that reads the true future. An upper bound, never a method. |
| **Feasibility map** | The alive/dead boundary over footstep energy and footsteps per visit. |
| **Visit rule** | How motion events become visits. `node` merges a node's zones (lower bound on walkers); `zone` gives each zone its own stream (upper bound). |

---

## 3. The system in one picture

```
ENERGY (design: target hardware, not in the Wokwi project)

  footstep ──► floor tile ──► piezo discs ──┐
  furniture ─► compression pad ─────────────┤
  door ──────► magnet + coil (flyby) ───────┼──► rectifier (0.55) ──► PMIC (0.60) ──► STORE (440.6 mJ)
  exercise ──► generator (synthetic) ───────┤                                          │
  light ─────► indoor PV cell (30 µW) ──────┘                                          │ leakage + MCU sleep
                                                                                       │ (~717 mJ/day, always)
                                                                                       ▼
SENSING NODE (ESP32-C3, Wokwi project)                                      ┌─────────────────────┐
  store level ─► mode (HIGH / MEDIUM / LOW / CRITICAL) ─► sensing rate       │ spend: sense + send │
  PIR motion ──┐                                                             └─────────┬───────────┘
  DHT22 temp ──┼─► MCU ─► OLED / LED                                                    │
  button ──────┘                                                                        ▼
                                                                    ESP-NOW / BLE (~7.8 mJ per packet)
                                                                                        │
MAINS-POWERED (not on harvested energy)                                                 ▼
  gateway ──► Wi-Fi / MQTT ──► broker (broker.hivemq.com) ──► hub (hub.py) ──► schedule command
                                                                                        │
                                                                    back down the same route to the node
```

Only the node runs on harvested energy. The gateway, broker and hub are
mains-powered and sit outside the energy budget.

---

## 4. Team and ownership

| Member | Area | Main files |
|---|---|---|
| Reet (M1) | Node firmware and the Wokwi build | `firmware/` |
| Hitakshi (M2) | Dataset processing and forecasting baselines | `src/fetch_casas.py`, `build_events.py`, `make_windows.py`, `baseline.py`, `casas_io.py` |
| Nainika (M3) | Energy model, simulator, schedulers, experiments, hub | `src/storage.py`, `harvesting.py`, `schedulers.py`, `sim.py`, `run.py`, `oracle_bound.py`, `sensitivity.py`, `gait_energy.py`, `neural_forecast.py`, `hub.py`, `config/energy.yaml`, `tests/` |

`config/interface.schema.json` is shared. Changing it needs a commit that touches
both sides, plus a message to the other two.

Work happens on a branch per topic, with pull requests into `main`. The branch
currently in use is `oracle-bound`.

---

## 5. Data

### 5.1 Activity data: CASAS smart-home logs

Source: CASAS Smart Home datasets (Washington State University), Zenodo record
15708568, file `labeled_data.zip`. We download single testbed CSVs with HTTP range
requests (`src/fetch_casas.py`), so the 236 MB archive is never downloaded whole.
The classic Aruba dataset is no longer served, so we use the HH and MN series.

Each log line has no header:
```
2011-06-16,07:12:03.441,OutsideDoor,ON,Enter_Home="begin"
```
That is date, time, sensor or zone, state, and an optional activity label.

| Home | Residents | Days | Motion and door events | Sleep-labelled intervals | Use |
|---|---|---|---|---|---|
| hh104 | 1 | 61 | about 127,800 | 732 | Primary trace |
| hh101 | 1 | 60 | about 90,000 | 326 | Secondary |
| hh107 | 2 | 31 | about 87,000 | 12 | Multi-resident (few sleep labels) |
| mn50 | 2 | 15 | about 38,300 | 150 | Replication |
| mn51 | more than 2 | 30 | about 79,600 | 118 | Replication (36 h gap) |

Resident counts come from the Zenodo record description. They cannot be inferred
from the home names. All five homes are older-adult or community settings.

**Zone-to-node mapping** (`config/nodes.yaml`, frozen):

| Virtual node | Built from |
|---|---|
| floor | Kitchen and LivingRoom motion |
| ambient | WorkArea, Bathroom, DiningRoom, Office, Hall motion (varies by home) |
| furniture | LoungeChair motion (hh104 only) and Bedroom motion during Sleep labels |
| door | OutsideDoor opening events |
| gym | Synthetic. CASAS has no exercise sensing |

### 5.2 Gait data: footstep energy

Source: Horst et al. (2017), "A public dataset of overground walking kinetics in
healthy individuals", Gait & Posture 58:476–480, CC BY 4.0,
https://data.mendeley.com/datasets/yrpbd8fhc4/1.

- `gait/gait_grf_subject.txt`: 128 subjects with mass, height, age, shoe size.
- `gait/gait_grf_rawdata/`: 1,278 walking trials with ground-reaction force at
  1000 Hz (about 500 MB).

The `gait/` folder is gitignored. Download it manually; the calibration table
derived from it is committed (`config/gait_calibration.csv`).

### 5.3 What the data does not contain

- **No exercise sensing.** The gym node is synthetic.
- **No pet sensing.** A pet and a person look the same to the sensors.
- **No count of simultaneous walkers.** CASAS does not say who is moving.
- **Thin compression data.** Chair motion exists only in hh104. Sleep labels
  range from 732 in hh104 down to 12 in hh107.
- **No electrical output of a piezo disc** under a real footstep.
- **No measured sleep current** of the node.

### 5.4 Data coverage, summarised

| Source | Enough? |
|---|---|
| Footsteps (floor, ambient) | Yes: 15 to 61 days, 38,000 to 127,000 events per home |
| Doors | Yes for counts; the energy is tiny |
| Furniture compression | No: thin outside hh104 |
| Exercise | No real data |
| Pets | Cannot be separated |
| Simultaneous walkers | Bracketed by the two visit rules, not counted |

---

## 6. The energy model

Units are millijoules (mJ) throughout. Every constant lives in
`config/energy.yaml`, with a provenance tag: `MEASURED`, `DATASHEET`, `DERIVED`,
`PLACEHOLDER` or `SWEPT`. No energy number belongs in Python code.

### 6.1 From events to energy

```
motion events ──► visits (visit rule) ──► × footsteps per visit (SWEPT 5–80)
                                          × raw energy per footstep (25.3 mJ, DERIVED from gait data)
                                          × rectifier (0.55) × PMIC (0.60)
                                          = mJ into the store
```

The four sources, each with its own raw value per event and multiplier:

| Source | Which events | Raw energy per event | Multiplier | Into the store |
|---|---|---|---|---|
| Footstep | floor and ambient motion | 25.3 mJ (DERIVED) | footsteps per visit (SWEPT 5–80) | 8.4 mJ per footstep |
| Compression | lounge-chair motion, bedroom sleep presence | 0.25 mJ (PLACEHOLDER) | 2 per visit | 0.165 mJ per visit |
| Door flyby | door openings (never merged) | 0.40 mJ (PLACEHOLDER) | 1 | 0.132 mJ per opening |
| Exercise | synthetic sessions, 4 per week | 180 kJ per 20-minute session (PLACEHOLDER) | 1 | about 59,400 mJ per session |
| Light | indoor PV, occupancy-independent | 30 µW peak (SWEPT 0–200) | daylight curve, 12 h | added directly |

### 6.2 Deriving footstep energy from body mass

```
F_peak  = k · m · g           k = 1.20 per foot (MEASURED from the gait trials)
E_mech  = ½ · F_peak · δ      δ = tile deflection, 2 mm (ASSUMED, SWEPT 1–3 mm)
E_elec  = E_mech · η          η = piezo coupling, 3 % (ASSUMED, SWEPT 1–5 %)
```

- The raw trials confirm F ∝ m: peak force slope 10.9 N/kg against 10.8 predicted.
- Population median at δ = 2 mm and η = 3 %: **25.3 mJ** per step (5th–95th
  percentile: 18 to 32 mJ).
- **Validation is partial.** The range matches published floor tiles (11 mJ for
  under 50 kg, 32 mJ for over 70 kg), but δ and η were chosen, not derived. The
  mass ratio does not match (model 1.89, published 2.91), and no choice of δ or η
  can fix that, because both scale the two groups equally.
- E_elec is raw piezo output, before the rectifier and PMIC. If the published
  figures are delivered energy, the model under-predicts about three times.

### 6.3 Storage

- Usable energy: ½ · C · (V_max² − V_min²) = ½ · 0.047 · (5.0² − 2.5²) = **440.6 mJ**.
- Update each second: `E ← clip(E + harvest − spend − leak, 0, E_max)`. Energy
  above E_max is counted as wasted.
- The selected option is **A** (0.047 F). Option B (0.10 F) is larger but leaks
  five times more. Option C (a lithium cell, 60 kJ) is untested. The conclusions
  depend on the choice.

### 6.4 Standing overhead

- Leakage: 1 µA × 5 V × one day = 432 mJ/day.
- MCU sleep: 1 µA × 3.3 V × one day = 285 mJ/day. The ESP32-C3 datasheet deep-sleep
  current is about 5 µA on a dev board, which would be about 1,400 mJ/day. The
  1 µA value is a placeholder, and this is the largest unknown in the model.
- Total used: about **717 mJ/day**, paid every second whether or not the node
  senses.

### 6.5 Task costs (ESP32-C3, placeholders)

| Task | Energy |
|---|---|
| Occupancy sense | 3.9 mJ |
| Environment sense (DHT22) | 2.4 mJ |
| Door sense | 1.1 mJ |
| Alarm (not used in the sweeps) | 28.1 mJ |
| ESP-NOW packet | 7.8 mJ |
| Wi-Fi/MQTT publish | 789 mJ (about 100× an ESP-NOW packet, so nodes must not publish directly) |

One sensing round (occupancy, environment, one packet) costs about **14.1 mJ**.

### 6.6 Modes

| Store level | Mode | Sense every |
|---|---|---|
| 70 % and above | HIGH | 5 s |
| 40 to 69 % | MEDIUM | 20 s |
| 15 to 39 % | LOW | 60 s |
| Below 15 % | CRITICAL | never (alarm reserve only) |

The same ladder is in `firmware/node.ino` (`modeFromSoc`) and `src/hub.py`
(`LADDER`). If you change one, change both.

### 6.7 Visit rules

The number of simultaneous walkers is unknown, so motion is turned into visits in
one of two ways:

- **`node`** (lower bound): the zones of one node are merged into one stream.
  Two people in different rooms count once.
- **`zone`** (upper bound): each zone is its own stream. One person moving between
  two rooms within a minute counts twice.

Door openings are never merged. Each is one flyby.

Measured effect on floor-node energy (full trace): from 12 % to 88 % more under
`zone` than `node`. The increase does not follow the resident count, which
suggests `zone` mostly counts one person moving between rooms.

### 6.8 Pets

Not modelled. Pets cannot be separated from people in the data. The planned
treatment is a swept pet-activity fraction, with the pet-to-human energy ratio
stated as an assumption (see section 10).

---

## 7. Schedulers and forecasters

### 7.1 Schedulers

All share one interface, `decide(store, t, ctx) → (sense, transmit)`, so new
policies drop in unchanged. Defined in `src/schedulers.py`.

| Name | Uses a forecast | What it does |
|---|---|---|
| S0 fixed duty | No | Senses every 15 steps regardless of energy |
| S1 reactive | No | Senses whenever it can afford to |
| S2 fixed reserve | No | Holds back a constant 25 % of capacity |
| S3 forecast reserve | Yes | Holds back energy for the predicted silence plus an alarm reserve |
| S4 forecast rate | Yes | Adjusts the sensing period so affordable senses are spread over the horizon |

Two design rules: the reserve must be set **before** the silence, not after it
starts; and sensing and transmitting use separate reserve floors.

### 7.2 Forecasters

| Forecaster | What it predicts | Role |
|---|---|---|
| Persistence (`baseline.py`) | Event count: last window, same time yesterday, same time last week | Baseline |
| Hour-of-day mean (`baseline.py`) | Event count: average for this node and hour | Baseline |
| Silence profile (`SilenceForecaster`) | Probability of harvest by time of day, and the length of the next silence | Used by S3 and S4 |
| Oracle (`OracleForecaster`) | The true future | Upper bound only |
| GRU (`neural_forecast.py`) | Next 15-minute event count, from the previous 24 hours, for all four nodes | Accuracy experiment, not a scheduler |

The GRU has one hidden layer of 32 units, trained with Adam (learning rate 0.001),
masked MSE on log counts, early stopping with patience 15, and a 300-epoch cap.
It was trained on hh104 alone, and then on the training splits of all five homes
pooled. Each home is scored on its own test split against its own baselines.

---

## 8. Metrics

| Metric | Meaning |
|---|---|
| **Capture during silence** (primary) | Fraction of injected critical events sensed and reported within deadline, during silence |
| Outage fraction | Fraction of time a node is out of energy |
| Information latency | Time from detection to report |
| Detections reported per day | Information yield |
| Energy wasted | Overflow above E_max, counted separately from wasted sensing |

Every result is reported as mean ± standard deviation over at least five seeds.
A gap smaller than the seed standard deviation is **not** a result. The oracle
check uses a go/no-go rule: if the oracle's gain over the histogram is inside the
seed noise, a trained model cannot pay for itself.

---

## 9. Results so far

Each finding is in `docs/findings.md` with the commands that produce it.

| # | Finding | Key numbers |
|---|---|---|
| **F1** | Perfect forecasting is worth almost nothing | Oracle gain over the histogram: at most +0.002 across five homes (default PV). Inside seed noise. |
| **F2** | At the old 0.6 mJ placeholder, the energy budget is the bottleneck | Kinetic-only harvest per day: floor 114, ambient 273, furniture 7.6, door 6.4 mJ, against about 717 mJ overhead. |
| **F3** | No real source harvests during the others' silence | Chair motion covers 13.7 % of the windows where floor, door and ambient are all silent. |
| **F4** | The door node is not viable as specified | 6.4 mJ/day against 717 mJ/day overhead: about 110 times short. |
| **F5** | Footstep energy from gait data moves the boundary | At 25.3 mJ, floor and ambient are alive under S4 from 6 and 2 mJ respectively (20 footsteps per visit). Furniture and door stay dead on kinetic energy alone. |
| **F6** | The GRU forecaster is mixed | Five homes pooled: MAE 4.90 against 6.07 for persistence; occupancy accuracy 0.733 against 0.771; RMSE and F1 both better. Accuracy only, not a scheduling gain. |

### 9.1 The feasibility map

The sweep varies footstep energy (0.1 to 32 mJ) and footsteps per visit (5 to
80) for each node and scheduler. A node is **alive** if its outage is below 50 %.

- **Kinetic only, S4, node visit rule:** the floor node is alive from 6 mJ at 10
  to 20 footsteps per visit, and from 2 mJ at 40 to 80. Ambient is alive from 2 mJ
  at 10 to 20 footsteps per visit. Furniture and door are never alive.
- **Kinetic only, S4, zone visit rule (upper bound):** the floor node is alive
  from 6 mJ at 5 footsteps per visit and from 0.6 mJ at 80.
- **Under reactive S1,** the floor node needs about 25 mJ at 40 footsteps per visit.
- **With indoor light at 30 µW,** almost every node is alive under S4, including
  the furniture and door nodes. That result depends on the light, so the
  kinetic-only result is the one to quote.

Plots: `reports/committed/sweep_hh104_pv0.png`,
`reports/committed/sweep_hh104_pv0_visitzone.png`,
`reports/committed/sweep_hh104_pv30.png`.

### 9.2 Simulator summary (hh104, kinetic only)

From `src/run.py`: under kinetic-only harvest, the furniture and door nodes are
excluded as starved. Floor and ambient carry signal. Floor capture during silence
is 0.68 under fixed duty (S0), 0.05 under reactive S1, and 0.82 under S3 and S4.
Ambient is 0.90 under S0 and 0.92 under S2 to S4. The gap between S1 and the
reserve-based schedulers is the largest effect in the study.

### 9.3 What the results do not show

- They show no scheduling gain from prediction.
- They show nothing about the furniture or door nodes on kinetic energy alone,
  which are dead under every assumption tested.
- They are based on five older-adult or community homes, at most two months each.
- Every energy value except the event rates and the gait-derived footstep energy
  is a placeholder.

---

## 10. What the project does not yet do

- **Pets.** Not modelled. Plan: a swept pet-activity fraction with an assumed
  pet-to-human energy ratio. Needs animal gait data or a cited literature range.
- **Simultaneous walkers.** Bounded by the two visit rules, not counted. The
  upper-bound results are in F5 but not in the F1 table.
- **Exercise energy.** The 180 kJ session is a placeholder. No real data.
- **Compression energy per sit.** The 0.25 mJ figure is a placeholder. The chair
  data gives how often people sit, not how much energy a sit produces.
- **Electrical output of a piezo disc** under a real footstep. The 3 % coupling
  and 2 mm deflection are assumptions.
- **Sleep current** of the node. The 1 µA figure is a placeholder.
- **Closed-loop hardware test.** `hub.py` has passed its self-test, but nobody has
  run the Wokwi node. The loop is not closed.
- **LSTM or other sequence model** beyond the GRU. Not started.
- **RL and MILP.** Not started. Neither is on the critical path, and plan §15
  allows dropping MILP.

---

## 11. Hardware, firmware and the hub

### 11.1 The node (`firmware/node.ino`, ESP32-C3 in Wokwi)

| Part | Pin | Role |
|---|---|---|
| Potentiometer | GPIO 2 (ADC) | Stands in for the store level in standalone mode |
| PIR motion sensor | GPIO 3 | Presence detection (one visit, not one footstep) |
| Pushbutton | GPIO 4 | Simulated event |
| LED | GPIO 5 | Lit during a sensing round |
| DHT22 | GPIO 10 | Temperature and humidity |
| OLED SSD1306 | I²C: SDA 8, SCL 9, address 0x3C | Shows level, mode, interval, last event |

Behaviour: reads the store level, picks a mode, senses at that rate, publishes its
state, and follows the last schedule command received.

**Before running:** set `PREFIX` in `node.ino` to a name unique to the team
(currently `ehsh-CHANGEME`). The broker is public, so collisions are possible.
Libraries are in `firmware/libraries.txt`. `firmware/diagram.json` is the Wokwi
project.

**Not verified:** the sketch has never been compiled or run in Wokwi.
`firmware/REET_START_HERE.md` is the step-by-step guide for Reet.

### 11.2 Messages (`config/interface.schema.json`)

- **Node → hub**, on `ehsh-<team>/<node_id>/state`: `node_id`, `ts`,
  `stored_energy_pct`, `mode`, `sampling_interval_s`, optionally `last_event`,
  `sensor` (temperature, humidity) and `fw`.
- **Hub → node**, on `ehsh-<team>/<node_id>/schedule`: `node_id`, `mode`,
  `sampling_interval_s`, `schedule_ts`, optionally `stored_energy_pct` and
  `task_set`.

Both sides must agree on these fields. The firmware's fields match the schema.

### 11.3 The hub (`src/hub.py`)

- Subscribes to node state, picks a mode with the threshold ladder, and publishes
  a schedule command.
- Modes: `--prefix <team>` (live), `--record log.jsonl`, `--replay log.jsonl` (for a
  demo when the network fails), `--selftest` (no broker needed; passes).
- The forecaster and scheduler slot in between "parse" and "publish" later.

### 11.4 The 3D model (`docs/hardware_3d.html`)

An interactive 3D model of the node and its four energy sources, with an animated
simulation of the energy and data flow. Open it in a browser (needs internet for
Three.js). It runs the same harvest, overhead and mode rules as the Python model,
with one difference: light follows the same 12-hour daylight curve.

---

## 12. Reproducing everything

Run from the repository root.

```bash
pip install -r requirements.txt            # torch is needed for the GRU only

# 1. Data (per home: hh104 hh101 hh107 mn50 mn51)
python src/fetch_casas.py hh104            # downloads one testbed log
python src/build_events.py hh104           # raw log -> per-node events
python src/make_windows.py hh104           # events -> 15-minute windows

# 2. Gait-derived footstep energy (needs the gait/ folder, see 5.2)
python src/gait_energy.py                  # writes config/gait_calibration.csv

# 3. Tests and self-checks
python -m pytest tests -q                  # 11 tests: energy conservation, mapping, visit rules
python src/hub.py --selftest               # hub decision logic, no broker

# 4. Simulator and experiments (hh104, kinetic only = --pv 0)
python src/run.py hh104 --days 20 --seeds 5 --pv 0          # scheduler comparison
python src/oracle_bound.py hh104 --pv 0 --days 20 --seeds 5 # forecasting go/no-go
python src/sensitivity.py hh104 --pv 0                      # feasibility map (node rule)
python src/sensitivity.py hh104 --pv 0 --visit zone         # feasibility map (zone rule)
python src/concurrency.py                                   # visit-rule bounds, five homes

# 5. Forecasting
python src/baseline.py hh104               # naive baselines
python src/neural_forecast.py hh104        # GRU, one home
python src/neural_forecast.py hh104 hh101 hh107 mn50 mn51   # GRU, pooled

# 6. Dashboard
streamlit run dashboard/app.py
```

Runtimes: `build_events` about a minute; `run.py` a few minutes;
`oracle_bound` a few minutes; `sensitivity` about three minutes; the GRU a few
minutes for three seeds on one home, and longer when pooled across five.

The 3D model is `docs/hardware_3d.html`. Open it in a browser.

Raw data (`data/raw/`), processed data (`data/processed/`), the gait archive
(`gait/`), and all of `reports/` except `reports/committed/` are gitignored and
regenerated by the commands above. `reports/committed/` holds the results the
findings quote.

---

## 13. Repository layout

```
.
├── README.md                          this file
├── requirements.txt                   Python dependencies (torch for the GRU)
├── Energy_Harvesting_Smart_Home_Full_Project_Plan.docx   the design document
│
├── config/
│   ├── energy.yaml                    every energy constant, with provenance tags
│   ├── nodes.yaml                     room → node mapping per home
│   ├── gait_calibration.csv           per-subject footstep energy (committed)
│   └── interface.schema.json          node ↔ hub message format (shared)
│
├── src/
│   ├── fetch_casas.py                 download one CASAS testbed (range requests)
│   ├── casas_io.py                    shared reader for CASAS logs
│   ├── build_events.py                raw log → per-node events
│   ├── make_windows.py                events → 15-minute windows, with splits
│   ├── explore_dataset.py             exploratory checks on a testbed
│   ├── validate_debounce.py           checks the visit rule against ground truth
│   ├── silence_analysis.py            silence-interval analysis (F3)
│   ├── baseline.py                    persistence and hour-of-day baselines
│   ├── gait_energy.py                 footstep energy from body mass (F5)
│   ├── harvesting.py                  events → mJ, four sources, visit rules
│   ├── storage.py                     the store and the config loader
│   ├── schedulers.py                  S0–S4, silence profile, oracle
│   ├── sim.py                         the closed loop for one node
│   ├── run.py                         scheduler comparison (writes metrics)
│   ├── oracle_bound.py                forecasting go/no-go (F1)
│   ├── sensitivity.py                 feasibility sweep (F5)
│   ├── concurrency.py                 visit-rule bounds across five homes
│   ├── neural_forecast.py             GRU forecaster (F6)
│   └── hub.py                         MQTT hub (selftest, live, replay)
│
├── tests/
│   ├── test_storage.py                energy conservation and reserve rules
│   └── test_harvesting.py             source mapping and visit rules
│
├── firmware/
│   ├── node.ino                       ESP32-C3 node sketch
│   ├── diagram.json                   Wokwi project
│   ├── libraries.txt                  libraries for Wokwi
│   ├── README.md                      technical reference
│   └── REET_START_HERE.md             guided setup for the node
│
├── dashboard/
│   └── app.py                         Streamlit results explorer
├── .streamlit/config.toml             dashboard theme (light)
│
├── docs/
│   ├── findings.md                    F1–F6 with commands and open items (read this)
│   ├── hardware_3d.html               interactive 3D model of the node
│   └── member3-brief.md               original brief for the simulator track (historical)
│
├── data/                              gitignored: raw and processed data
├── gait/                              gitignored: gait dataset (downloaded manually)
└── reports/
    ├── committed/                     results quoted by the findings
    └── (others)                       gitignored, regenerated by the scripts
```

---

## 14. Decisions still open

These need a decision from the team before the write-up:

1. **Four sources, or footsteps only.** Recommendation: keep all four in the
   simulation, headline the footstep boundary, and report the others as dead or
   assumption-dependent.
2. **Storage option.** A (selected) or C (lithium, untested). Changes the gym and
   overall conclusions.
3. **Published tile figures.** Raw piezo output or delivered energy. Changes the
   validation of the footstep energy.
4. **Visit rule for the headline.** Report both bounds, or pick one with a stated reason.
5. **MILP and RL.** Keep or drop (plan §15).

---

## 15. Conventions

- **Energy values** carry a provenance tag in `config/energy.yaml`. Add no energy
  number without one.
- **Never quote a PV-on result alone.** Report kinetic-only (`--pv 0`) next to it.
- **Seeds.** At least five. A gap smaller than the seed standard deviation is not
  a result.
- **Oracle results** are upper bounds. Never report them as a method.
- **Forecasting results** are accuracy results, not scheduling results, unless a
  scheduler shows a gain larger than the seed noise.
- **Shared files.** Change `config/interface.schema.json` only in a commit that
  touches both the firmware and the hub, and message the other two members.
- **Mode ladder.** `node.ino` and `hub.py` must match.
- **Commit messages.** Describe what changed and why, and state any result that a
  reviewer should check.

---

## 16. History and superseded material

- `docs/findings.md` records what each finding changed. Read it in order.
- `docs/member3-brief.md` is the original simulator brief. Parts are superseded;
  its status note says which.
- Earlier numbers quoted in old notes, such as the 3.7 mJ door figure, the 7.5 mJ
  door figure, and the 33,500 mJ gym figure, are historical. The current values are
  in `config/energy.yaml` and `docs/findings.md`.
- The earlier results with `--pv 0` used the 0.6 mJ footstep placeholder. The
  current kinetic-only results use the gait-derived footstep energy.
