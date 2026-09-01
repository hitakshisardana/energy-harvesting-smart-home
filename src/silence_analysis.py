"""
Silence-interval analysis — the Phase 0 gate for the reserve-scheduling claim.

A "silence" is a run of consecutive windows in which a node harvests nothing.
Everything downstream depends on its distribution:

  * If median silence is short (< ~30 min), no reserve policy matters and the
    project should fall back to simple threshold scheduling.
  * The forecast lookahead must exceed the store's recharge time, which is set
    by how long silences actually are.
  * A tuned CONSTANT reserve beats a forecast-driven one when silences are
    regular. The forecast only earns its complexity if the distribution is
    dispersed / heavy-tailed. Look at IQR and p99/median, not just the median.

Two framings, deliberately kept apart:

  PER-NODE silence  — the energy question. Storage is local and there is NO
                      transfer between nodes, so this is what a node's own
                      reserve policy must survive.
  COVERAGE silence  — the scheduling question. A window where NO node has
                      harvested recently, i.e. no node can take over another's
                      sensing duty. This is the MILP layer's justification.
                      It is NOT a claim about pooled energy.

Gym is synthetic and is therefore excluded from the headline numbers and
reported only as an ablation. Its trace is conditioned on real HH104 occupancy
so it cannot generate exercise sessions while the house is empty.

Usage:
    python src/silence_analysis.py hh104
    python src/silence_analysis.py hh104 --freq 15min --seed 7
Output: reports/silence_<trace>/  (metrics.json, three PNGs)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"

REAL_NODES = ["floor", "furniture", "ambient", "door"]
NIGHT = range(0, 7)          # hours counted as "night" for interval tagging


# --------------------------------------------------------------------------
# silence runs
# --------------------------------------------------------------------------
def silence_runs(counts: pd.Series, step_min: float) -> pd.DataFrame:
    """
    Every maximal run of zero-count windows.

    Returns one row per interval: start time, duration in minutes, the hour it
    began, and a diurnal tag. Night silence and daytime-absence silence are
    different phenomena with different forecastability, so they are tagged
    rather than pooled.
    """
    z = counts.eq(0).to_numpy()
    idx = counts.index
    runs, i = [], 0
    while i < len(z):
        if not z[i]:
            i += 1
            continue
        j = i
        while j < len(z) and z[j]:
            j += 1
        start = idx[i]
        runs.append({
            "start": start,
            "n_windows": j - i,
            "minutes": (j - i) * step_min,
            "start_hour": int(start.hour),
            "kind": "night" if start.hour in NIGHT else "day",
            "dow": int(start.dayofweek),
        })
        i = j
    return pd.DataFrame(runs)


def describe(runs: pd.DataFrame, label: str) -> dict:
    if runs.empty:
        return {"label": label, "n_intervals": 0}
    m = runs.minutes
    d = {
        "label": label,
        "n_intervals": int(len(m)),
        "median_min": round(float(m.median()), 1),
        "mean_min": round(float(m.mean()), 1),
        "iqr_min": round(float(m.quantile(.75) - m.quantile(.25)), 1),
        "p90_min": round(float(m.quantile(.90)), 1),
        "p99_min": round(float(m.quantile(.99)), 1),
        "max_min": round(float(m.max()), 1),
        "frac_time_silent": None,     # filled by caller
    }
    # dispersion is what decides whether a CONSTANT reserve is already optimal
    d["p99_over_median"] = round(d["p99_min"] / max(d["median_min"], 1e-9), 2)
    d["cv"] = round(float(m.std() / max(m.mean(), 1e-9)), 2)
    return d


# --------------------------------------------------------------------------
# synthetic gym, conditioned on real occupancy
# --------------------------------------------------------------------------
def synth_gym(grid: pd.DatetimeIndex, occupancy: pd.Series,
              step_min: float, seed: int = 7) -> pd.Series:
    """
    Exercise sessions sampled ONLY from windows where HH104 shows the home
    occupied and awake. An independent schedule would generate workouts while
    the house is empty, which is physically impossible; conditioning on real
    occupancy makes gym 'modeled from HH104' rather than invented.

    Hour-of-day propensity is still an assumption (morning + evening peaks) and
    is declared as such. Everything gym-derived is reported as an ablation.
    """
    rng = np.random.default_rng(seed)
    hours = grid.hour.to_numpy()

    # ASSUMPTION (not measured): when people exercise, given they are home.
    propensity = np.zeros(24)
    propensity[6:9] = 0.35        # morning
    propensity[17:21] = 1.00      # evening peak
    propensity[9:17] = 0.08       # occasional daytime
    p = propensity[hours] * occupancy.to_numpy()

    # target ~4 sessions/week, each ~20 min
    sess_min = 20.0
    n_days = (grid[-1] - grid[0]).total_seconds() / 86400
    target_sessions = max(1, int(round(4 * n_days / 7)))
    if p.sum() > 0:
        p = p / p.sum()
    starts = rng.choice(len(grid), size=target_sessions, replace=False, p=p) \
        if p.sum() > 0 else np.array([], dtype=int)

    counts = np.zeros(len(grid))
    span = max(1, int(round(sess_min / step_min)))
    for s in starts:
        counts[s:s + span] = 1.0
    return pd.Series(counts, index=grid, name="gym")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def analyse(name: str, freq: str = "15min", seed: int = 7) -> dict:
    path = PROC / f"events_{name}.parquet"
    if not path.exists():
        sys.exit(f"missing {path} — run build_events.py first")
    ev = pd.read_parquet(path)

    step_min = pd.Timedelta(freq) / pd.Timedelta("1min")
    ev["w"] = ev.ts.dt.floor(freq)
    grid = pd.date_range(ev.w.min(), ev.w.max(), freq=freq)

    counts = (ev.groupby(["w", "node_id"]).size()
                .unstack("node_id", fill_value=0)
                .reindex(grid, fill_value=0))
    for n in REAL_NODES:
        if n not in counts:
            counts[n] = 0

    # occupancy proxy: any motion anywhere in the home this window
    occupancy = (counts[REAL_NODES].sum(axis=1) > 0).astype(float)
    counts["gym"] = synth_gym(grid, occupancy, step_min, seed)

    out, per_node_runs = {"trace": name, "freq": freq, "seed": seed,
                          "n_windows": int(len(grid)),
                          "days": round(len(grid) * step_min / 1440, 1)}, {}

    # ---- 1. per-node silence (the ENERGY question) ----
    rows = []
    for node in REAL_NODES + ["gym"]:
        runs = silence_runs(counts[node], step_min)
        per_node_runs[node] = runs
        d = describe(runs, node)
        d["source"] = "synthetic" if node == "gym" else "real"
        if node == "gym":
            # ~35 sessions over 61 days: the silence stats below are
            # small-sample noise (median swung 1425-2760 min across four
            # seeds). Do NOT report them as a finding. Only the coverage
            # ablation is stable, and that is stable by construction.
            d["sample_limited"] = True
        d["frac_time_silent"] = round(float(counts[node].eq(0).mean()), 3)
        d["events_per_day"] = round(float(counts[node].sum() /
                                          (len(grid) * step_min / 1440)), 1)
        for k in ("night", "day"):
            sub = runs[runs.kind == k] if not runs.empty else runs
            d[f"median_min_{k}"] = (round(float(sub.minutes.median()), 1)
                                    if len(sub) else None)
        rows.append(d)
    out["per_node"] = rows

    # ---- 2. coverage silence (the SCHEDULING question) ----
    # NOT pooled energy — storage is local. This asks: is there any node with
    # recent harvest that could take over another node's sensing duty?
    cov_real = counts[REAL_NODES].sum(axis=1)
    cov_all = counts[REAL_NODES + ["gym"]].sum(axis=1)
    runs_real = silence_runs(cov_real, step_min)
    runs_all = silence_runs(cov_all, step_min)

    a = describe(runs_real, "coverage_real_only")
    a["source"] = "real"
    a["frac_time_silent"] = round(float(cov_real.eq(0).mean()), 3)
    b = describe(runs_all, "coverage_with_synthetic_gym")
    b["source"] = "real + synthetic (ABLATION)"
    b["note"] = ("Expected to be IDENTICAL to coverage_real_only by "
                 "construction: gym sessions are sampled only from windows "
                 "where the home is occupied, and occupancy is defined as "
                 "some real node having fired. Every gym-active window is "
                 "therefore already covered. This shows a source active "
                 "ONLY when other sources are active adds no temporal "
                 "diversity - NOT that adding sources never helps. An "
                 "occupancy-independent source (indoor PV) is untested.")
    b["frac_time_silent"] = round(float(cov_all.eq(0).mean()), 3)
    out["coverage"] = [a, b]

    # ---- 3. does furniture harvest during everyone else's silence? ----
    others = counts[["floor", "ambient", "door"]].sum(axis=1)
    others_silent = others.eq(0)
    out["furniture_night_check"] = {
        "windows_others_silent": int(others_silent.sum()),
        "of_those_furniture_active": int((others_silent &
                                          counts["furniture"].gt(0)).sum()),
        "coverage_of_others_silence": round(float(
            (others_silent & counts["furniture"].gt(0)).sum() /
            max(1, others_silent.sum())), 3),
        "note": "MEASURED: furniture covers only 13.7% of the windows where "
                "floor/door/ambient are all silent, and its night and day "
                "silence medians are identical (45 min). It is NOT the "
                "anti-correlated night source the original design assumed. "
                "No real source in hh104 harvests during the others' silence; "
                "every one is occupancy-coupled. An occupancy-INDEPENDENT "
                "source (indoor PV) remains the only untested candidate.",
    }

    # ---- report ----
    outdir = REPORTS / f"silence_{name}"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics.json").write_text(json.dumps(out, indent=2))

    pn = pd.DataFrame(rows).set_index("label")
    print(f"\n{'='*76}\nPER-NODE SILENCE  ({name}, {freq} windows, "
          f"{out['days']} days)\n{'='*76}")
    print(pn[["source", "events_per_day", "frac_time_silent", "n_intervals",
              "median_min", "p90_min", "p99_min", "max_min",
              "p99_over_median", "cv"]].to_string())
    print("\n  median silence by time of day (min):")
    print(pn[["median_min_night", "median_min_day"]].to_string())

    print(f"\n{'='*76}\nCOVERAGE SILENCE — no node available to take over duty\n"
          f"(NOT pooled energy: storage is local, no transfer)\n{'='*76}")
    print(pd.DataFrame(out["coverage"]).set_index("label")[
        ["source", "frac_time_silent", "n_intervals", "median_min",
         "p90_min", "max_min"]].to_string())

    fc = out["furniture_night_check"]
    print(f"\nFurniture covers {fc['coverage_of_others_silence']:.1%} of the "
          f"windows where floor/door/ambient are all silent "
          f"({fc['of_those_furniture_active']:,}/{fc['windows_others_silent']:,})")

    # ---- plots ----
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))

    ax = axes[0]
    for node in REAL_NODES:
        r = per_node_runs[node]
        if r.empty:
            continue
        m = np.sort(r.minutes.to_numpy())
        ax.plot(m, np.arange(1, len(m) + 1) / len(m), label=node, lw=1.4)
    r = per_node_runs["gym"]
    if not r.empty:
        m = np.sort(r.minutes.to_numpy())
        ax.plot(m, np.arange(1, len(m) + 1) / len(m), "k--", lw=1,
                label=f"gym (synthetic, seed={seed})")
    ax.set_xscale("log")
    ax.set(title="Silence duration CDF, per node",
           xlabel="silence length (min, log)", ylabel="P(X <= x)")
    ax.axvline(60, color="grey", lw=.6, ls=":")
    ax.axvline(480, color="grey", lw=.6, ls=":")
    ax.legend(fontsize=7)

    ax = axes[1]
    for runs, lbl, st in [(runs_real, "real nodes only", "-"),
                          (runs_all, "+ synthetic gym (ablation)", "--")]:
        if runs.empty:
            continue
        m = np.sort(runs.minutes.to_numpy())
        ax.plot(m, np.arange(1, len(m) + 1) / len(m), st, lw=1.5, label=lbl)
    ax.set_xscale("log")
    ax.set(title="Coverage silence CDF", xlabel="silence length (min, log)",
           ylabel="P(X <= x)")
    ax.legend(fontsize=7)

    ax = axes[2]
    hourly = counts[REAL_NODES + ["gym"]].copy()
    hourly["hour"] = hourly.index.hour
    prof = hourly.groupby("hour").apply(
        lambda g: (g[REAL_NODES + ["gym"]] > 0).mean(), include_groups=False)
    for node in REAL_NODES:
        ax.plot(prof.index, prof[node], label=node, lw=1.3)
    ax.plot(prof.index, prof["gym"], "k--", lw=1,
            label=f"gym (synthetic, seed={seed})")
    ax.set(title="P(harvesting) by hour of day",
           xlabel="hour", ylabel="fraction of windows active",
           xticks=range(0, 24, 3))
    ax.legend(fontsize=7)

    fig.tight_layout()
    fig.savefig(outdir / "silence.png", dpi=125)
    plt.close(fig)
    print(f"\nWrote {outdir}/metrics.json and silence.png")
    return out


def main():
    args = sys.argv[1:]
    freq = "15min"
    seed = 7
    if "--freq" in args:
        freq = args[args.index("--freq") + 1]
    if "--seed" in args:
        seed = int(args[args.index("--seed") + 1])
    names = [a for a in args if not a.startswith("--")
             and a not in (freq, str(seed))] or ["hh104"]
    for n in names:
        analyse(n, freq, seed)


if __name__ == "__main__":
    main()