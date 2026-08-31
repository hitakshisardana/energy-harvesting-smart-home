"""
CLI: run every scheduler on a real trace and write metrics + plots.

    python src/run.py hh104
    python src/run.py hh104 --days 30 --mult 5 --pv 30
    python src/run.py hh104 --tune          # sweep the S2 reserve fraction first
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from storage import load_config
from harvesting import build_harvest, REAL_NODES, DEBOUNCE_S
from schedulers import (S0_FixedDuty, S1_Reactive, S2_FixedReserve,
                        S3_ForecastReserve, S4_ForecastRate)
from sim import run_node

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def factories(s2_frac, lookahead):
    return [lambda fc: S0_FixedDuty(15),
            lambda fc: S1_Reactive(),
            lambda fc: S2_FixedReserve(s2_frac),
            lambda fc: S3_ForecastReserve(fc, 1.5, lookahead),
            lambda fc: S4_ForecastRate(fc, 1.5, lookahead)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", nargs="?", default="hh104")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--step", type=float, default=60)
    ap.add_argument("--mult", type=float, default=None, help="footsteps per motion event")
    ap.add_argument("--pv", type=float, default=None, help="indoor PV in uW")
    ap.add_argument("--lookahead", type=int, default=720)
    ap.add_argument("--s2-frac", type=float, default=0.25)
    ap.add_argument("--tune", action="store_true", help="sweep S2 reserve first")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--debounce", type=float, default=None,
                    help="seconds; gap above which two detections are separate "
                         "visits. Validate per trace with validate_debounce.py "
                         "-- hh101's floor sensor is far chattier than hh104's "
                         "and needs a wider window.")
    ap.add_argument("--min-yield", type=float, default=0.5,
                    help="secondary constraint: fraction of reactive yield "
                         "still reported (rarely binding -- batching means one "
                         "transmission flushes the whole queue)")
    ap.add_argument("--max-latency", type=float, default=30.0,
                    help="PRIMARY constraint, minutes. What degrades when a "
                         "node senses rarely is FRESHNESS, not volume: an "
                         "occupancy detection delivered 8 h late is worthless. "
                         "Volume is nearly scheduler-independent, so latency "
                         "is the real cost axis.")
    ap.add_argument("--polled", action="store_true",
                    help="reproduce the unphysical polled baseline (sense "
                         "attempted every step regardless of any event)")
    ap.add_argument("--sweep", action="store_true",
                    help="emit the capture-vs-yield trade-off curve (main result)")
    a = ap.parse_args()

    cfg = load_config()
    harvest, events, grid = build_harvest(a.trace, cfg, a.step, a.mult, a.pv,
                                          a.seed, a.days,
                                          a.debounce or DEBOUNCE_S)
    nodes = REAL_NODES + ["gym"]
    print(f"{a.trace}: {len(grid):,} steps @ {a.step:g}s "
          f"({len(grid)*a.step/86400:.1f} days)")
    print("\nmJ/day by node:")
    print((harvest.sum() / (len(grid) * a.step / 86400)).round(1).to_string())

    # --- which nodes are actually alive? a starved node measures starvation,
    # --- not policy, so it must not enter a scheduler comparison.
    alive, ref_yield = [], {}
    for n in nodes:
        r, _, _ = run_node(n, harvest, events, cfg,
                           lambda fc: S1_Reactive(), a.step, seed=a.seed,
                           interrupt_driven=not a.polled)
        ref_yield[n] = r["reported_per_day"]
        if r["outage_frac"] < 0.9 and r["reported_per_day"] > 0:
            alive.append(n)
    starved = [n for n in nodes if n not in alive]
    if starved:
        print(f"\nEXCLUDED (starved, >90% outage or zero yield): {starved}")
    print(f"nodes carrying signal: {alive}")

    # --- tune S2 subject to a YIELD FLOOR ---------------------------------
    # Tuning on capture alone always runs to the ceiling: reserve=1.0 means
    # "never sense", which wins the capture metric by refusing to do the job.
    # A reserve fraction is only meaningful under a constraint on information.
    # Each node has its own store and runs its own policy, so tuning them
    # JOINTLY lets the weakest node cap the strongest. Tune per node.
    cmp_nodes = [n for n in alive if n != "gym"]
    s2_frac = {n: a.s2_frac for n in nodes}
    if a.tune:
        print(f"\ntuned S2 reserve fraction, per node "
              f"(latency ceiling {a.max_latency:g} min, yield floor {a.min_yield:.0%})")
        for n in cmp_nodes:
            best, rows_t = None, []
            for f in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]:
                r, _, _ = run_node(n, harvest, events, cfg,
                                   (lambda fr: (lambda fc: S2_FixedReserve(fr)))(f),
                                   a.step, seed=a.seed,
                                   interrupt_driven=not a.polled)
                lat = r["mean_latency_min"]
                bad = []
                if not np.isfinite(lat) or lat > a.max_latency:
                    bad.append("LATENCY")
                if r["reported_per_day"] < a.min_yield * ref_yield[n]:
                    bad.append("YIELD")
                rows_t.append((f, r["capture_silence"], lat, "ok" if not bad
                               else "violates " + "+".join(bad)))
                if not bad and (best is None or r["capture_silence"] > best[1]):
                    best = (f, r["capture_silence"])
            s2_frac[n] = a.s2_frac if best is None else best[0]
            print(f"  [{n}] -> frac {s2_frac[n]}")
            for f, c, lat, note in rows_t:
                lat_s = f"{lat:7.1f}" if np.isfinite(lat) else "    inf"
                print(f"     frac {f:<5} capture {c:<6.3f} latency {lat_s} min  {note}")

    rows, traj = [], {}
    for node in nodes:
        for fac in factories(s2_frac[node], a.lookahead):
            r, soc, res = run_node(node, harvest, events, cfg, fac, a.step,
                                   seed=a.seed, interrupt_driven=not a.polled)
            r["source"] = "synthetic" if node == "gym" else "real"
            rows.append(r)
            traj[(node, r["scheduler"])] = (soc, res)
    df = pd.DataFrame(rows)

    assert df.balance_err.max() < 1e-6, "energy not conserved"

    outdir = REPORTS / f"sim_{a.trace}"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "metrics.csv", index=False)
    (outdir / "run.json").write_text(json.dumps(vars(a) | {"s2_frac": s2_frac}, indent=2))
    print("\n=== INFORMATION LATENCY (min) - the real cost of sensing rarely ===")
    print(df[(df.source == "real") & (df.node.isin(alive))]
          .pivot(index="node", columns="scheduler",
                 values="mean_latency_min").round(1).to_string())

    real = df[(df.source == "real") & (df.node.isin(alive))]
    print("\n=== CRITICAL-EVENT CAPTURE DURING SILENCE (primary metric) ===")
    print(real.pivot(index="node", columns="scheduler",
                     values="capture_silence").round(3).to_string())
    print("\n=== DETECTIONS REPORTED PER DAY (information yield) ===")
    print(real.pivot(index="node", columns="scheduler",
                     values="reported_per_day").round(0).to_string())
    print("\n=== OUTAGE FRACTION ===")
    print(real.pivot(index="node", columns="scheduler",
                     values="outage_frac").round(3).to_string())

    # --- the trade-off curve: the main result --------------------------------
    # A single (capture, yield) point cannot rank two schedulers -- they sit at
    # different operating points. Sweep each policy's parameter and compare the
    # CURVES. The curve that sits above the other wins, at every yield level.
    if a.sweep:
        pts = []
        grids = {
            "S0 fixed duty":       [("period", p) for p in [5, 15, 30, 60, 120, 240, 480]],
            "S2 fixed reserve":    [("frac", f) for f in [0.0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0]],
            "S3 forecast reserve": [("safety", k) for k in [0.5, 1, 2, 4, 8, 16, 32, 64]],
            "S4 forecast rate":    [("safety", k) for k in [0.5, 1, 2, 4, 8, 16, 32, 64]],
        }
        for node in [n for n in alive if n != "gym"]:
            for fam, grid_pts in grids.items():
                for pname, pv in grid_pts:
                    if fam == "S0 fixed duty":
                        fac = (lambda v: (lambda fc: S0_FixedDuty(int(v))))(pv)
                    elif fam == "S2 fixed reserve":
                        fac = (lambda v: (lambda fc: S2_FixedReserve(v)))(pv)
                    elif fam == "S3 forecast reserve":
                        fac = (lambda v: (lambda fc: S3_ForecastReserve(
                            fc, v, a.lookahead)))(pv)
                    else:
                        fac = (lambda v: (lambda fc: S4_ForecastRate(
                            fc, v, a.lookahead)))(pv)
                    r, _, _ = run_node(node, harvest, events, cfg, fac,
                                       a.step, seed=a.seed,
                                       interrupt_driven=not a.polled)
                    pts.append({"node": node, "family": fam, "param": pname,
                                "value": pv,
                                "capture_silence": r["capture_silence"],
                                "reported_per_day": r["reported_per_day"],
                                "mean_latency_min": r["mean_latency_min"],
                                "yield_frac": r["reported_per_day"] / max(ref_yield[node], 1e-9)})
        sw = pd.DataFrame(pts)
        sw.to_csv(outdir / "sweep.csv", index=False)

        cmp_nodes = sorted(sw.node.unique())
        fig2, axs = plt.subplots(1, len(cmp_nodes), figsize=(5.2 * len(cmp_nodes), 4.4),
                                 squeeze=False)
        for ax, node in zip(axs[0], cmp_nodes):
            g = sw[sw.node == node]
            for fam, gg in g.groupby("family"):
                gg = gg[np.isfinite(gg.mean_latency_min)].sort_values("mean_latency_min")
                ax.plot(gg.mean_latency_min, gg.capture_silence, marker="o",
                        ms=4, lw=1.3, label=fam)
            ax.set(title=f"{node}: capture vs staleness",
                   xlabel="mean information latency (min, log)",
                   ylabel="critical capture during silence")
            ax.set_xscale("log")
            ax.axvline(a.max_latency, color="grey", ls=":", lw=.8)
            ax.legend(fontsize=7)
        fig2.tight_layout()
        fig2.savefig(outdir / "tradeoff.png", dpi=125)
        print(f"\nWrote {outdir}/sweep.csv and tradeoff.png")
        print("\n=== TRADE-OFF: best capture within the latency ceiling ===")
        adm = sw[(sw.mean_latency_min <= a.max_latency)
                 & np.isfinite(sw.mean_latency_min)
                 & (sw.yield_frac >= a.min_yield)]
        if len(adm):
            best = (adm.sort_values("capture_silence", ascending=False)
                       .groupby(["node", "family"]).head(1)
                       .sort_values(["node", "capture_silence"], ascending=[True, False]))
            print(best[["node", "family", "param", "value", "capture_silence",
                        "mean_latency_min", "yield_frac"]].round(3).to_string(index=False))
        else:
            print("  no configuration meets the latency ceiling")

    # --- plots ---
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    for ax, metric, title in [
            (axes[0], "capture_silence", "Critical capture during silence"),
            (axes[1], "reported_per_day", "Detections reported / day")]:
        real.pivot(index="node", columns="scheduler", values=metric) \
            .plot(kind="bar", ax=ax)
        ax.set_title(title)
        ax.legend(fontsize=7)
        ax.tick_params(axis="x", rotation=0)

    ax = axes[2]
    sl = slice(0, int(3 * 86400 / a.step))
    for sched in ["S1 reactive", "S3 forecast reserve"]:
        soc, res = traj[("floor", sched)]
        ax.plot(soc[sl], lw=.9, label=sched)
    _, res = traj[("floor", "S3 forecast reserve")]
    cfgmax = 1.0
    ax.plot(res[sl] / max(res.max(), 1e-9) * cfgmax, "k--", lw=.8,
            label="S3 reserve (scaled)")
    ax.set(title="Floor node state of charge, 3 days",
           xlabel="step", ylabel="SoC")
    ax.legend(fontsize=7)

    fig.tight_layout()
    fig.savefig(outdir / "sim.png", dpi=125)
    print(f"\nWrote {outdir}/metrics.csv and sim.png")


if __name__ == "__main__":
    main()