# Simulator / integration track — full brief for Member 3

*Read top to bottom before starting. Self-contained. Pairs with the revised plan
(`Energy_Harvesting_Smart_Home_Full_Project_Plan.docx`, Sections 7, 13–15, 22–25,
29, 30) and `config/interface.schema.json`.*

---

## 1. TL;DR

You own the **Python software simulator** — the closed loop that everything else
plugs into:

```
event stream (from M2)  →  harvesting model  →  per-node energy store
        →  task demand  →  scheduler decision  →  execute / defer / sleep
        →  energy updated  →  next step  →  metrics + plots
```

Plus the **MQTT hub** that talks to Reet's Wokwi node, and later the RL
environment, the optional MILP layer, and end-to-end integration. You are the
integration lead, but integration is shared — see §10.

You can start **today** with placeholder numbers and a synthetic event stream;
you don't have to wait for anyone.

---

## 2. Where this fits

| | M2 (Hitakshi) | **You (M3)** | M1 (Reet) |
|---|---|---|---|
| Produces | event stream, activity + energy forecasts | the simulator, schedulers, experiments, results | one hardware node + firmware |
| You consume | M2's `events_*.parquet` and forecasts | — | M1's node state over MQTT |
| You provide | — | schedule commands, metrics, the Gym env | — |

The research question (does predictive + coordinated scheduling beat
fixed/reactive under the same harvested energy?) is answered **by your
experiments**. Everything else is input to that.

---

## 3. Files you own

```
config/
  nodes.yaml            zone→node map, task priorities, E_max, E_reserve   ← you + M2 agree, freeze in Phase 0
  energy.yaml           J per event, task energy costs, capacitor C/V/leak ← placeholders + source per line
src/
  storage.py            one node's supercapacitor state (the equation)     ← start here
  tasks.py              task catalogue: cost, priority, deadline, cadence
  harvesting.py         event → Joules into a node's store (draw from a distribution)
  schedulers.py         FixedScheduler, ThresholdScheduler (later: RL, MILP) — one interface
  sim.py                the time-step loop that ties it together
  run.py                CLI: pick trace + scheduler + days, run, write plots + metrics.json
  hub.py                MQTT: subscribe PREFIX/+/state, publish PREFIX/<id>/schedule
  envs/energy_env.py    Gym wrapper around sim.py (Phase 6)
tests/
  test_storage.py       energy never < 0 or > E_max; conservation checks
```

Do **not** touch `data/`, `fetch_casas.py`, `explore_dataset.py`, or the
forecasting code — that's M2. Do **not** touch `firmware/` — that's M1.
`config/interface.schema.json` is shared: change it only in a commit that also
pings both teammates.

---

## 4. The energy equation (get this exactly right — plan Section 7)

```
E[t+1] = clip( E[t] + eta_in * E_harvested[t] - E_task[t] - E_leak[t],  0,  E_max )

  eta_in    : input / PMIC conversion efficiency  (0..1, placeholder 0.5)
  E_leak[t] : supercap self-discharge this step   (placeholder: 0.1 %/hour of E_max)
  E_task[t] : sum of energy of tasks ACTUALLY executed this step
```

Rules:
- A task `i` may execute in step `t` **only if** `E[t] >= E_task_i + E_reserve`.
  `E_reserve` is energy ring-fenced so an alarm can always fire.
- Energy that would push `E` above `E_max` is **lost** — accumulate it as
  `wasted_overflow`.
- Only the charge between the regulator's `V_min` and full `V_max` is usable;
  if you model in volts, `E_usable = 0.5*C*(V**2 - V_min**2)`. If you model in
  Joules directly, just set `E_max` to the *usable* figure and note it.

`storage.py` skeleton:

```python
from dataclasses import dataclass

@dataclass
class Store:
    e_max: float          # J, usable
    e: float              # J, current
    eta_in: float = 0.5
    leak_per_step: float = 0.0   # J lost per step (from energy.yaml)
    wasted_overflow: float = 0.0

    def step(self, harvested_j: float, task_j: float) -> None:
        gained = self.eta_in * harvested_j
        raw = self.e + gained - task_j - self.leak_per_step
        if raw > self.e_max:
            self.wasted_overflow += raw - self.e_max
        self.e = min(max(raw, 0.0), self.e_max)

    def can_run(self, cost_j: float, reserve_j: float) -> bool:
        return self.e >= cost_j + reserve_j
```

Write `tests/test_storage.py` first: random harvest/task sequences, assert
`0 <= e <= e_max` always, assert conservation (in − out − leak − wasted == Δe).

---

## 5. The scheduler interface (freeze this shape now)

Every scheduler — fixed, threshold, RL, MILP — is the same callable:

```python
def decide(node_state: NodeState, forecast: Forecast | None,
           pending: list[Task]) -> list[Task]:
    """Return the subset of `pending` to execute this step."""
```

- `FixedScheduler`: run a task whenever its fixed period has elapsed, energy
  permitting.
- `ThresholdScheduler`: run pending tasks only while `E[t] > threshold` (e.g.
  40 % of `E_max`); always allow `alarm`.
- Later `RLScheduler`, `MILPScheduler` slot in unchanged. `forecast` is already a
  parameter so M2's forecasts drop in without touching `sim.py`.

Baselines you must implement for the main experiment (Section 24): fixed duty
cycle, energy threshold, predictive-independent, policy-only, policy+MILP. Fix
each one's exact constants in `config/` and record them with results.

---

## 6. The loop (`sim.py`)

```python
for t in timeline(step_seconds):
    for node in nodes:
        harv = sum(harvesting.energy(e) for e in events[node] at t)
        pending = tasks.due(node, t)
        chosen  = scheduler.decide(node.state(t), forecast_at(t), pending)
        task_j  = 0.0
        for task in chosen:
            if node.store.can_run(task.cost_j, cfg.e_reserve):
                task_j += task.cost_j
                log_done(t, node, task)
            else:
                log_skipped(t, node, task, reason="energy")
        node.store.step(harv, task_j)
    log_row(t, {n.id: n.store.e for n in nodes})
```

Timeline step: start at **10 s** (fast, plenty of resolution). Events come from a
parquet with columns `ts, node_id, event_type, zone`.

---

## 7. Metrics (`run.py` writes `reports/<trace>_<sched>/metrics.json`)

Use the Section 23 definitions exactly:

| metric | definition |
|---|---|
| tasks_completed | executed on time |
| task_completion_rate | completed_on_time / requested |
| critical_events_captured | injected critical event sensed **and** "transmitted" within its deadline |
| energy_wasted_overflow | Σ energy lost above `E_max` |
| energy_wasted_redundant | energy spent on sensing later flagged redundant (log separately) |
| outage_time_s | total time a node is below the energy for its minimum task |
| energy_to_info | useful sensing outputs per Joule harvested |

Every run: fix a `--seed`, run **≥ 5 seeds**, report mean ± std. Single-run
deltas are not evidence.

---

## 8. Your milestones

| # | Goal | Done when… | Needs |
|---|---|---|---|
| **S0** | `storage.py` + `tests/test_storage.py` green | energy bounds + conservation hold under random input | nothing |
| **S1** | `sim.py` runs on a **synthetic** event stream with `FixedScheduler` | produces a storage-trajectory PNG for 5 nodes over 24 h | S0 |
| **S2** | `ThresholdScheduler` + `metrics.json` | `run.py --scheduler threshold` writes all §7 metrics | S1 |
| **S3** | Runs on **real** `events_hh104.parquet` | numbers are physically plausible (no node permanently dead, no infinite energy) | M2's `build_events.py` output |
| **S4** | `hub.py` MQTT echo loop | Reet's Wokwi node changes mode from a command your hub sends off its own state — **Phase 3 milestone** | `interface.schema.json`, Reet at M3 |
| **S5** | `envs/energy_env.py` Gym wrapper | `env.step(action)` advances `sim.py` one step, returns obs/reward | S2 |

S0–S2 are pure solo work with placeholders. Start now.

---

## 9. `hub.py` — the bit that meets Reet

First version is deliberately dumb (no ML):

```
subscribe  PREFIX/+/state
on message:
    parse NodeState
    mode = "HIGH" if soc > 70 else "MEDIUM" if soc > 40 else "LOW" if soc > 15 else "CRITICAL"
    publish ScheduleCommand(node_id, mode, interval_for(mode)) to PREFIX/<node_id>/schedule
```

Broker `broker.hivemq.com:1883`, same `PREFIX` string Reet picks. Test against
her Wokwi node directly. Once this closed loop works, insert forecasting → policy
→ (MILP) between "parse" and "publish" — nothing else changes.

Add `paho-mqtt` to the env: `pip install paho-mqtt`.

---

## 10. Integration is shared

You lead it, but M1 and M2 must each be able to run the loop with **stubs** for
the parts they don't own, via the frozen interface. Provide from week 1:

- a `--fake-events` flag in `run.py` (synthetic stream) so nobody waits on M2
- a `--replay <mqtt log>` mode so the demo survives a flaky broker
- `NodeState` / `ScheduleCommand` dataclasses in `src/messages.py` that both you
  and M2 import (single source of truth, matches the JSON schema)

---

## 11. Git workflow

```bash
git clone <REPO_URL> && cd energy-harvesting-smart-home
git checkout -b sim-core

git add src/ tests/ config/
git commit -m "sim: S0 storage model + tests"
git push -u origin sim-core         # then just: git push
```

PR `sim-core` → `main` at each milestone. Pull `main` often:
`git checkout main && git pull && git checkout sim-core && git merge main`.

Never commit to `main`. Never edit `data/`, `firmware/`, or
`config/interface.schema.json` without a heads-up.

---

## 12. Environment

Already present: numpy, pandas, matplotlib, scipy, scikit-learn, gymnasium.
Add now: `pip install paho-mqtt pyyaml`. Add later: `torch` (only if you do
PPO — Q-learning/heuristic needs nothing), `pulp` (Phase 7 MILP).

---

## 13. First session checklist

1. Clone, `git checkout -b sim-core`, `pip install paho-mqtt pyyaml`.
2. Draft `config/nodes.yaml` + `config/energy.yaml` with placeholder numbers,
   each value commented with a source or `# PLACEHOLDER`.
3. Write `src/storage.py` + `tests/test_storage.py` → **S0**.
4. Write `src/messages.py` (dataclasses matching `interface.schema.json`).
5. Synthetic event generator + `sim.py` loop + `FixedScheduler` → **S1**.
6. PR.

---

## 14. Questions

- Event stream schema / forecast format → Hitakshi (M2).
- MQTT topics / message fields / Wokwi behaviour → Reet (M1).
- Scope, what counts as "done", whether to build MILP → team + the revised plan.
