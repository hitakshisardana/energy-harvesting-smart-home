# Findings so far

Measured results, and what they rule out. Read this before starting new work —
two of them close off phases the plan schedules weeks for.

All numbers: trace hh104 (61 days, single-resident apartment) unless stated,
60 s timestep, seeds 0–4, mean ± std. Energy follows the four-source mapping in
`src/harvesting.py` (footstep, furniture compression, door flyby, exercise).
Footstep energy is the gait-derived value (F5); the other energy constants are
placeholders tagged in `config/energy.yaml`. Reproduce with the commands in each
section. Raw output in `reports/committed/`.

---

## F1. Perfect forecasting is worth ~nothing on this trace

```bash
python src/oracle_bound.py hh104 --days 20 --seeds 5          # default PV (30 uW)
python src/oracle_bound.py hh104 --pv 0 --days 20 --seeds 5   # kinetic only
```

The table below uses the default run, which includes indoor light. Under
kinetic-only harvest (`--pv 0`) the furniture and door nodes are dead, so their
rows are not informative there; see `reports/committed/oracle_hh104_pv0.txt`.

`OracleForecaster` reads the true future harvest. No forecaster — histogram,
LSTM, transformer — can beat it, so it bounds what the whole forecasting track
can ever deliver.

Replicated across **five homes spanning the resident dimension**. Resident
counts are from the Zenodo record description (15708568), which publishes a
per-home table -- they are NOT inferable from the testbed prefix, and an
earlier version of this repo asserted the wrong ones.

| home | residents | days | capture range (S4, default PV) | best S4 oracle gain |
|---|---|---|---|---|
| hh104 | 1 | 20 | 0.116-0.918 | +0.002 |
| hh101 | 1 | 20 | 0.122-0.962 | +0.000 |
| hh107 | 2 | 20 | 0.095-0.816 | +0.001 |
| mn50 | 2 | 14 | 0.119-0.956 | +0.000 |
| mn51 | >2 | 20 | 0.158-0.842 | +0.000 |

Capture levels vary by about an order of magnitude across these homes; the oracle
gap does not. The oracle gains are at most +0.002 (S4). Of the 40 (home, node,
scheduler) comparisons, one clears its seed standard deviation: the door node
under S3 in hh104 (+0.015 against an SD of 0.013). That node is dead on real
data, and its capture here comes from indoor light, so it is not a finding.

Detail for hh104 (20 days, 5 seeds, default PV), mean capture during silence:

| node | S1 no forecast | S3 hist | S3 oracle | S4 hist | S4 oracle |
|---|---|---|---|---|---|
| floor | 0.030 | 0.863 | 0.863 | 0.866 | 0.866 |
| furniture | 0.000 | 0.094 | 0.107 | 0.118 | 0.120 |
| ambient | 0.106 | 0.918 | 0.918 | 0.918 | 0.918 |
| door | 0.000 | 0.080 | 0.095 | 0.116 | 0.115 |
| gym (synthetic) | 0.003 | 0.351 | 0.351 | 0.353 | 0.353 |

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

## F2. The energy budget, not the policy, is what is failing (at the placeholder)

```bash
python src/run.py hh104 --days 20 --seeds 5 --pv 0
```

This finding holds at the **old 0.6 mJ footstep placeholder**. With indoor PV
off, the harvest per node is 114 mJ/day (floor), 273 (ambient), 7.6 (furniture)
and 6.4 (door), against about 717 mJ/day of standing overhead (supercapacitor
leakage plus MCU sleep). At the placeholder, all four nodes are below overhead,
so every node is starved, and the scheduler comparison is not informative.

At the gait-derived footstep energy (F5), the floor (4,792 mJ/day) and ambient
(11,513 mJ/day) nodes are well above overhead. The furniture (7.6 mJ/day) and
door (6.4 mJ/day) nodes stay below it, because their sources do not depend on
footsteps. So the energy budget is the bottleneck for the furniture and door
nodes, whatever the footstep value.

The default run only looks alive for these nodes because indoor PV supplies most
of their energy. PV is occupancy-independent, out of the plan's stated scope,
and has no measurement behind it.

**Consequence.** Until standing overhead sits below daily harvest for a node,
the scheduler has nothing to allocate and no ML layer can help that node.
Attack overhead first (sub-µA MCU class, lower-leakage store, or a
transient-powered node with no persistent state), or reframe the project around
the feasibility boundary itself — see "What to do instead" below.

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

~52 events/day → ~6.4 mJ/day at placeholder conversion, against ~717 mJ/day of
overhead: a ~110× shortfall. Each opening is one magnet flyby, so door events
are not merged into visits (merging undercounted them: 3.7 mJ/day before the
fix). No transduction multiplier applies, so the parameter that rescues the
floor node cannot rescue this one.

**Consequence.** Re-scope it as a transient, unpowered, event-triggered reporter
with no store and no sleep current, or drop it from the node set. It should not
appear in a scheduler comparison; a starved node measures starvation, not policy.
`run.py` already excludes it automatically.

---

## F5. Footstep energy from gait data moves the feasibility boundary

Footstep energy was a placeholder (0.6 mJ/step) with no justification. It is
now derived from body mass in the Horst et al. (2017) gait dataset (128
subjects, 1278 GRF trials, CC BY 4.0) through

    F_peak = k·m·g,  E_mech = ½·F_peak·δ,  E_elec = E_mech·η

Measured from the raw trials: k = 1.20 per foot (IQR 1.17–1.25), and peak force
scales with mass at 10.9 N/kg against 10.8 predicted. Tile deflection δ and
piezo coupling η are **assumptions**, swept over 1–3 mm and 1–5 %. At the
population median (δ = 2 mm, η = 3 %) the value is 25 mJ/step (5–95 %: 18–32).
The per-subject table is `config/gait_calibration.csv`.

**Validation against published floor tiles is partial.**
- Range: passes. The model gives 15 mJ (<50 kg) and 29 mJ (>70 kg) against
  published 11 and 32, but δ and η were not derived independently, so this is
  a consistency check, not a test.
- Ratio: fails. The model's >70 kg / <50 kg ratio is 1.89; the published ratio
  is 2.91. Because F ∝ m is confirmed from raw data, this gap is not fixable by
  tuning δ or η. It is either in the published numbers (different tiles, speeds
  or population) or in a non-linear deflection effect we do not model.
- Units: E_elec is raw piezo output, before the rectifier × PMIC chain (0.33).
  If the published figures are delivered energy, the model under-predicts about
  3×.

**Sweep** (`src/sensitivity.py`, hh104, kinetic only, PV off, 20 days, 5 seeds,
1400 runs). Alive means outage below 50 %. The boundary is in
`reports/committed/sweep_hh104_pv0.png`.

Lowest footstep energy at which a node is alive (outage < 50 %), from
`reports/committed/sweep_hh104_pv0.csv`, under the four-source mapping:

| node | source | S4 forecast rate (20 footsteps/visit) | S1 reactive (40 footsteps/visit) |
|---|---|---|---|
| floor | footstep | ≥ 6 mJ | ≥ 25 mJ |
| ambient | footstep | ≥ 2 mJ | ≥ 6 mJ |
| furniture | compression | never in the grid | never in the grid |
| door | door flyby | never | never |

At the old 0.6 mJ placeholder, no motion node is alive under S4 at 20
footsteps per visit, except ambient at 40 or more footsteps per visit.

**Visit rule: lower and upper bounds on concurrent walkers.** Motion events are
merged into visits, and two rules bracket the number of walkers. `by=node` merges
the zones of a node into one stream: a lower bound, since two people in
different rooms count once. `by=zone` gives each zone its own stream: an upper
bound, since one person moving between two rooms within a minute counts twice.
CASAS cannot say who is moving, so the truth lies between them.

| home | residents | floor mJ/day, node rule | floor mJ/day, zone rule | ratio |
|---|---|---|---|---|
| hh104 | 1 | 6,314 | 11,841 | 1.88 |
| hh101 | 1 | 25,913 | 28,927 | 1.12 |
| hh107 | 2 | 18,498 | 25,317 | 1.37 |
| mn50 | 2 | 15,335 | 21,553 | 1.41 |
| mn51 | >2 | 19,189 | 25,878 | 1.35 |

The increase does not follow the resident count. The single-resident hh104 has
the largest rise, which suggests the zone rule mostly counts one person moving
between rooms. Under the zone rule, the lowest footstep energy at which the floor
node is alive under S4 falls by about one step: 6 mJ at 5 footsteps per visit
(node rule: 11 mJ), and 0.6 mJ at 80 (node rule: 2 mJ). Furniture and door stay
dead under both rules. The forecasting null still holds under the zone rule.

**Consequence.** F2 is conditional. It holds at the old placeholder. At
gait-derived energies, the energy budget stops being the bottleneck for the
footstep-driven motion nodes under S4. The boundary also depends on the
scheduler: reactive S1 needs about 4× more energy per step to keep the floor
node alive. The furniture node is dead at every footstep energy in the grid,
because its only source is compression. The door node is dead for the same
reason as F4.

**With indoor PV at 30 µW** (`reports/committed/sweep_hh104_pv30.png`, not a claim on its
own): under S4 every node is alive from 0.1 mJ, except ambient at 5–10
footsteps per visit (0.6 mJ). The furniture and door nodes are alive only
because PV carries them, which is the reason the kinetic-only result is the
one to quote. Under S1 the lowest alive energy is not monotone in footsteps per
visit (floor: 6, 2, 6, 0.6 mJ at 5, 10, 20, 40 footsteps/visit), so single
cells from the S1 grid should not be quoted without seed error bars.

**Forecasting null re-checked** (`oracle_bound.py`, PV off, 5 seeds, 20 days).
Under the four-source mapping the oracle gain over the histogram is at most
+0.001 (gym, S3), against seed SD of 0.01–0.04. Floor and ambient show
+0.000. Furniture and door are dead, so their gains are zero by construction.
F1 still holds at this energy level.

Caveats: capture during silence rises a lot at high energy (S1 floor
0.05 → 0.82 under S4), so the S1-versus-forecast gap is dominated by reserving
at all, as F1 says. The gait value is not a calibrated energy figure for
CASAS visits. CASAS visits do not map to steps (CASAS does not record who is
walking), so the footsteps-per-visit axis is still a sensitivity sweep.

---

## F6. A trained GRU forecaster is mixed against the naive baselines

`src/neural_forecast.py`, hh104, 5 seeds, 3,456 test windows (covered target
and previous window). Forecasting accuracy only. F1 still applies: this does
not show a scheduling gain.

| method | MAE | RMSE | occupancy acc | occupancy F1 |
|---|---|---|---|---|
| persist_last | 5.73 | 15.84 | 0.692 | 0.516 |
| hour_mean | 7.55 | 18.54 | 0.440 | 0.495 |
| GRU (mean ± sd, 5 seeds) | 5.06 ± 0.04 | 17.67 ± 0.26 | 0.644 ± 0.01 | 0.558 ± 0.007 |

The GRU is better on MAE and occupancy F1 and worse on RMSE and occupancy
accuracy. Raising the epoch cap from 60 to 300 changed nothing: early stopping
triggered at 63 to 84 epochs. The seed spread is small, so the differences
are not seed noise. The RMSE result says the model misses the large bursts
that persist_last catches.

**Five homes pooled.** `python src/neural_forecast.py hh104 hh101 hh107 mn50 mn51`
trains one GRU on the training splits of all five homes, then scores each home's
own test split against that home's baselines (3 seeds, 9,860 test windows
pooled):

| scope | method | MAE | RMSE | occupancy acc | occupancy F1 |
|---|---|---|---|---|---|
| pooled | persist_last | 6.07 | 17.37 | 0.771 | 0.607 |
| pooled | hour_mean | 6.67 | 16.29 | 0.535 | 0.532 |
| pooled | GRU (3 seeds) | 4.90 | 15.94 | 0.733 | 0.630 |

Per home, the GRU beats persist_last on MAE in all five homes and on RMSE in four
(not hh104). Its occupancy accuracy is lower than persist_last in all five. Its
occupancy F1 is higher in four homes and level in hh107. The same forecasting
caveat applies: this is forecasting accuracy, not a scheduling gain (F1).

## What to do instead

The defensible contribution given F1–F4 is a **feasibility map**, not a
scheduling win:

> Under what parameter regime can occupancy-coupled kinetic harvesting fund an
> autonomous sensing node in a real home — and above that boundary, how much does
> scheduling policy actually matter?

The axes already exist as `SWEPT` entries in `config/energy.yaml`:
`mcu_sleep_ua`, `footsteps_per_visit`, `debounce_s`, the visit rule (node or zone),
`footstep` energy, `indoor_pv_uw`, and the two storage options. Sweeping them produces the dead/alive boundary, and F1 becomes a
result within it rather than a disappointment: *reserving matters, predicting
better does not, and here is the regime where either matters at all.*

Negative results with a rigorous sensitivity map are publishable at this level.
A manufactured 0.11-vs-0.09 win is not.

---

## Open items, in priority order

Done since the last revision: the starved-regime timeout (oracle walk made O(1)),
`sensitivity.py` (sweep written and run, F5), the four-source mapping, the door
flyby fix, the visit-rule bounds, and the GRU on five homes (F6).

1. **Decide on the four sources vs footsteps focus.** Recommendation in the
   discussion: keep all four in the simulation, headline the footstep boundary.
   Needs a decision before the write-up.
2. **Decide the storage option and the tile-figure question.** A (selected) or C
   (lithium, untested) changes the gym and overall conclusions. Whether the
   published tile numbers are raw or delivered energy changes the validation.
3. **Pets.** Not modelled. CASAS has no pet sensing, so a pet fraction can only
   be a labelled sweep. Needs an animal gait or energy source to set the
   pet-to-human ratio.
4. **Close the MQTT loop.** `hub.py` is selftested; nobody has run the Wokwi
   node (no toolchain here). Needs the team prefix in `firmware/node.ino` and a
   run in Wokwi. Graded.
5. **Measure one value.** Sleep current of the node, or one piezo disc under a
   real footstep. These decide the feasibility map more than any modelling.
6. **More data for compression and exercise.** Compression rests on hh104 chair
   data and thin sleep labels elsewhere (12 to 732). Exercise has no real data.
   More CASAS homes with sleep labels would help compression; exercise needs an
   external source.
7. **Re-run F1 under the zone visit rule** if the write-up uses the upper bound.
8. **Decide on RL/MILP.** On current evidence neither is on the critical path.
   Plan §15 already permits dropping MILP; say so explicitly rather than leaving
   it as implied scope.
