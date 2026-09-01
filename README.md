# Energy-Harvesting Smart Home — Predictive Scheduling Prototype

TRL 3 research prototype. Three-person capstone team. Full design:
`Energy_Harvesting_Smart_Home_Full_Project_Plan.docx`.

**Research question.** Given the same limited, intermittent harvested-energy
budget, can predictive forecasting and coordinated scheduling enable more useful
smart-home sensing than fixed or reactive energy-neutral operation?

**Where we are on answering it.** Partly, and the answer so far is not the one
the plan assumed. See `docs/findings.md` — read it before starting new work,
because it rules out two things the plan schedules weeks for.

---

## Quick start

```bash
pip install -r requirements.txt

python src/fetch_casas.py hh104       # ~14 MB out of a 236 MB Zenodo archive
python src/build_events.py hh104      # raw log -> per-node event stream
python -m pytest tests -q             # energy-conservation invariants

python src/run.py hh104 --days 20 --seeds 5        # scheduler comparison
python src/oracle_bound.py hh104 --days 20 --seeds 5   # is forecasting worth it?
python src/hub.py --selftest                        # MQTT decision logic
```

Runtime: `build_events` ~1 min, `run.py` a few minutes, `oracle_bound` ~3 min.
Raw and processed data are gitignored — regenerate with the scripts above.
Committed results live in `reports/committed/`.

---

## What this repo does

```
CASAS hh104 log ──> build_events ──> events_hh104.parquet
                                          │
                    ┌─────────────────────┴────────────────────┐
                    ▼                                          ▼
            make_windows / baseline              harvesting.py  (events -> mJ)
            (forecasting track, M2)                             │
                                                       storage.py (supercap)
                                                                │
                                                       schedulers.py (S0..S4)
                                                                │
                                                          sim.py (closed loop)
                                                                │
                                                     run.py  /  oracle_bound.py
                                                                │
                                              hub.py ──MQTT──> Wokwi ESP32-C3
```

**Schedulers.** All share one interface, so any new policy drops in unchanged.

| | forecast? | actuator |
|---|---|---|
| `S0_FixedDuty` | no | fixed sensing period |
| `S1_Reactive` | no | spend whenever affordable |
| `S2_FixedReserve` | no | constant reserve threshold |
| `S3_ForecastReserve` | yes | reserve sized from predicted silence |
| `S4_ForecastRate` | yes | sensing *period* from predicted silence |

`SilenceForecaster` is a time-of-day histogram — a persistence-class baseline,
and the bar any trained model must clear. `OracleForecaster` reads the true
future; it is an **upper bound**, never a result.

---

## Status by phase

| Phase | Scope | State |
|---|---|---|
| 0 | Freeze spec, interface, dataset | done (`config/`) |
| 1 | Baseline energy simulator | done, real trace, invariants tested |
| 2 | Wokwi node firmware | code written, **never verified to compile** |
| 3 | Wokwi ↔ Python MQTT loop | `hub.py` written + selftested; **loop not closed** |
| 4 | Forecasting | naive baselines only; LSTM not started (see findings) |
| 5 | Energy forecasting | not started |
| 6 | RL policy | not started |
| 7 | MILP | not started, may be dropped (plan §15) |
| 8 | End-to-end + dashboard | not started |
| 9 | Physical validation | not started — **all energy constants are placeholders** |
| 10 | Writeup | not started |

**Critical path right now:** Phase 3 (someone must run the Wokwi node), then the
parameter sensitivity map, then the writeup. Phases 6–7 are not on it.

---

## Ownership

| | Owner | Files |
|---|---|---|
| M1 hardware/firmware | Reet | `firmware/` |
| M2 dataset + forecasting | Hitakshi | `src/fetch_casas.py`, `build_events.py`, `make_windows.py`, `baseline.py`, `casas_io.py` |
| M3 simulator + integration | Nainika | `src/storage.py`, `harvesting.py`, `schedulers.py`, `sim.py`, `run.py`, `oracle_bound.py`, `hub.py`, `config/energy.yaml`, `tests/` |

`config/interface.schema.json` is **shared**. Changing it requires a commit that
touches both sides and a message to the other two.

Branch per member (`hw-node`, `sim-core`, …), PR into `main`. This has not been
followed so far — all work is on `main`.

---

## Two things to know before trusting any number

1. **Every energy constant except the event rates is a placeholder.** They carry
   `src:` tags in `config/energy.yaml` (`MEASURED` / `DATASHEET` / `DERIVED` /
   `PLACEHOLDER` / `SWEPT`). Nothing is bench-measured yet, so absolute results
   are not claimable — only relative comparisons under a stated parameter set,
   plus sensitivity across the `SWEPT` ranges.

2. **The default run leans on indoor PV**, which is out of the project's stated
   scope and untested. With `--pv 0` — the four kinetic sources the project is
   actually about — three of five nodes are starved and capture is zero. Report
   both, and never quote the PV-on number alone.
