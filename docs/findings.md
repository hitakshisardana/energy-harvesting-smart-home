# Findings so far

Measured results, and what they rule out. Read this before starting new work —
two of them close off phases the plan schedules weeks for.

All numbers: trace hh104 (61 days, single-resident apartment), 60 s timestep,
placeholder energy constants from `config/energy.yaml`, seeds 0–4, mean ± std.
Reproduce with the commands in each section. Raw output in
`reports/committed/`.

---

## F1. Perfect forecasting is worth ~nothing on this trace

```bash
python src/oracle_bound.py hh104 --days 20 --seeds 5
```

`OracleForecaster` reads the true future harvest. No forecaster — histogram,
LSTM, transformer — can beat it, so it bounds what the whole forecasting track
can ever deliver.

Replicated across **five homes spanning the resident dimension**. Resident
counts are from the Zenodo record description (15708568), which publishes a
per-home table -- they are NOT inferable from the testbed prefix, and an
earlier version of this repo asserted the wrong ones.

| home | residents | days | capture range (S4) | best S4 oracle gain |
|---|---|---|---|---|
| hh104 | 1 | 20 | 0.107-0.160 | +0.003 |
| hh101 | 1 | 20 | 0.035-0.194 | +0.001 |
| hh107 | 2 | 20 | 0.026-0.212 | +0.002 |
| mn50 | 2 | 14 | 0.055-0.190 | +0.000 |
| mn51 | >2 | 20 | 0.074-0.182 | +0.000 |

Capture levels vary by an order of magnitude across these homes; the oracle gap
does not. Every gain is inside the seed standard deviation.

Detail for hh104 (20 days, 5 seeds):

| node | S1 no forecast | S3 hist | S3 oracle | S4 hist | S4 oracle | seed sd |
|---|---|---|---|---|---|---|
| floor | 0.000 | 0.087 | 0.090 | 0.140 | 0.139 | 0.010 |
| furniture | 0.000 | 0.113 | 0.119 | 0.160 | 0.163 | 0.020 |
| ambient | 0.000 | 0.052 | 0.057 | 0.107 | 0.112 | 0.027 |
| door | 0.000 | 0.082 | 0.101 | 0.112 | 0.113 | 0.005 |
| gym (synthetic) | 0.000 | 0.352 | 0.352 | 0.354 | 0.354 | 0.029 |

Perfect knowledge buys between −0.001 and +0.006 over a 20-line time-of-day
histogram on S4. Every gap is inside seed noise; on floor it is negative. The
single case that clears noise (door, S3, +0.019) is the node we already know is
dead on real data, and S4 erases it.

**Mechanism.** The forecast enters `S4` as one integer `D`, the predicted
silence length, and acts only through
`spare = E − (overhead·D + alarm·safety)`. When `E` is near zero most of the
time, knowing `D` exactly does not create energy to ration. The large effect —
`S1 → S3/S4`, 0.00 → 0.14 — is *reserving at all versus not*. Prediction
**quality** contributes nothing on top.

**Consequence.** Training an LSTM to improve scheduling cannot pay for itself
here. If a trained model is required as a deliverable, train it against
`baseline.py` and report it as a *forecasting-accuracy* result, stating plainly
that better accuracy did not convert into better scheduling — with this table as
the evidence. Do not report a scheduling improvement from it.

**Scope.** Five homes across 1, 2 and >2 residents, but all are CASAS
retirement-community or community-dwelling older-adult homes with placeholder
energy constants and strongly diurnal occupancy (`silence_analysis.py` measures p99/median dispersion
of 5.3–6.0 on three of four nodes — close to regular, which is exactly where a
constant or histogram reserve is already near-optimal). A multi-occupant or
irregular home could differ. Re-run the oracle before assuming otherwise.

---

## F2. The energy budget, not the policy, is what is failing

```bash
python src/run.py hh104 --days 20 --seeds 5 --pv 0
```

With indoor PV off — i.e. the four kinetic sources the project is actually about
— harvest per node is 4–273 mJ/day against ~717 mJ/day of standing overhead
(supercapacitor leakage + MCU sleep). Three of five nodes are auto-excluded as
starved; the two survivors are in outage 64–87 % of the time and capture 0.000
critical events under *every* scheduler.

The default run only looks alive because indoor PV supplies roughly 85–90 % of
the energy. PV is occupancy-independent, out of the plan's stated scope, and
`src: SWEPT` with no measurement behind it.

**Consequence.** Until standing overhead sits below daily harvest, the scheduler
has nothing to allocate and no ML layer can help. Attack overhead first
(sub-µA MCU class, lower-leakage store, or a transient-powered node with no
persistent state), or reframe the project around the feasibility boundary
itself — see "What to do instead" below.

---

## F3. No real source in hh104 harvests during the others' silence

```bash
python src/silence_analysis.py hh104
```

The design assumed furniture (chair/mattress compression) was an anti-correlated
night source that would harvest while the rest of the home was quiet. Measured:
it covers **13.7 %** of the windows where floor/door/ambient are all silent
(351/2,556), with identical 45 min night and day silence medians.

Every real source in this trace is occupancy-coupled. Adding the synthetic gym
node changes coverage silence by 0.000 across four seeds — by construction,
since gym sessions are sampled only from occupied windows. That shows *a source
active only when other sources are active adds no temporal diversity*; it does
not show that adding sources never helps. An occupancy-independent source
(indoor PV) is the only untested candidate.

`DATA_NOTES.md` and `silence_analysis.py` both stated the wrong version of this
until it was corrected. Do not re-import the old claim from the plan document.

---

## F4. The door node is not viable as specified

~52 events/day → ~7.5 mJ/day at placeholder conversion, against ~717 mJ/day of
overhead: a ~96× shortfall. Unlike motion nodes, no transduction multiplier
applies — one door opening is one magnet flyby, so the parameter that rescues
the floor node cannot rescue this one.

**Consequence.** Re-scope it as a transient, unpowered, event-triggered reporter
with no store and no sleep current, or drop it from the node set. It should not
appear in a scheduler comparison; a starved node measures starvation, not policy.
`run.py` already excludes it automatically.

---

## What to do instead

The defensible contribution given F1–F4 is a **feasibility map**, not a
scheduling win:

> Under what parameter regime can occupancy-coupled kinetic harvesting fund an
> autonomous sensing node in a real home — and above that boundary, how much does
> scheduling policy actually matter?

The axes already exist as `SWEPT` entries in `config/energy.yaml`:
`mcu_sleep_ua`, `footsteps_per_visit`, `debounce_s`, `indoor_pv_uw`, and the two
storage options. Sweeping them produces the dead/alive boundary, and F1 becomes a
result within it rather than a disappointment: *reserving matters, predicting
better does not, and here is the regime where either matters at all.*

Negative results with a rigorous sensitivity map are publishable at this level.
A manufactured 0.11-vs-0.09 win is not.

---

## Open items, in priority order

0. **`--pv 0` still times out.** Fixed one O(n^2) pathology in the forecaster
   (`p_harvest` was called ~1e6 times per node; now precomputed in `fit`, a
   ~5x speedup that unblocked the multi-resident runs). A second, separate
   pathology remains in the starved regime -- which is exactly where the
   feasibility boundary lives, so this blocks the sweep.
1. **Close the MQTT loop.** `hub.py` is written and selftested; nobody has run
   the Wokwi node. It is a graded criterion and it is not on anyone's calendar.
2. **`sensitivity.py`** — the sweep described above. This is the contribution.
3. **Bench-measure anything.** One measured `footstep` mJ figure would move more
   of this document than any amount of modelling.
4. **Decide on RL/MILP.** On current evidence neither is on the critical path.
   Plan §15 already permits dropping MILP; say so explicitly rather than leaving
   it as implied scope.
