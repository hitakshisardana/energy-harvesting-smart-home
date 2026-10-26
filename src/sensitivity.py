"""
Feasibility sweep: where does kinetic harvesting fund a node?

Grid: footsteps_per_visit (SWEPT, CASAS visits are not bridgeable to steps)
      x footstep_mj (raw piezo mJ per step; literature range, see below)
Kinetic only: indoor PV off, because the project claims kinetic sources only.

For each cell, the node is ALIVE if its outage fraction is below ALIVE_OUTAGE
under the given scheduler. The boundary between alive and dead is the result.

Device classes are marked on the footstep axis so the boundary can be read
against real hardware:
  hobby piezo prototypes   under 1 mJ / step   (lower bound of literature)
  piezo disc arrays        ~2 mJ / step        (upper bound of discs)
  published floor tiles    11 mJ (<50 kg), 32 mJ (>70 kg)  (gait-derived range)

Usage
    python src/sensitivity.py hh104 --days 20 --seeds 5
Output
    reports/sweep_<trace>/sweep.csv        every run (raw)
    reports/committed/sweep_<trace>.csv    per-cell summary (small, committed)
    reports/committed/sweep_<trace>.png    boundary plot
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from storage import load_config
from harvesting import build_harvest, REAL_NODES, DEBOUNCE_S
from schedulers import S1_Reactive, S4_ForecastRate
from sim import run_node

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

FOOTSTEPS_PER_VISIT = [5, 10, 20, 40, 80]
FOOTSTEP_MJ = [0.1, 0.6, 2.0, 6.0, 11.0, 25.3, 32.0]
ALIVE_OUTAGE = 0.5          # alive = in outage less than half the time

DEVICE_CLASSES = [
    (0.6, "hobby piezo (old placeholder)"),
    (2.0, "piezo disc array, upper"),
    (11.0, "floor tile, <50 kg"),
    (32.0, "floor tile, >70 kg"),
]


def sweep(trace: str, days: float, seeds: int, step: float, lookahead: int,
          pv_uw: float = 0.0, visit_by: str = "node"):
    base = load_config()
    rows = []
    for fpv in FOOTSTEPS_PER_VISIT:
        for fs in FOOTSTEP_MJ:
            cfg = copy.deepcopy(base)
            cfg["harvest"]["per_event_raw_mj"]["footstep"]["value"] = fs
            harvest, events, grid = build_harvest(trace, cfg, step, fpv, pv_uw, 7,
                                                  days, DEBOUNCE_S, False, visit_by)
            for node in REAL_NODES:
                for sched_name, fac, fmode in [
                    ("S1 reactive", lambda fc: S1_Reactive(), "hist"),
                    ("S4 forecast rate",
                     lambda fc: S4_ForecastRate(fc, 1.5, lookahead), "hist"),
                ]:
                    for seed in range(seeds):
                        r, _, _ = run_node(node, harvest, events, cfg, fac, step,
                                           seed=seed, forecaster=fmode)
                        rows.append({
                            "trace": trace, "footsteps_per_visit": fpv,
                            "footstep_mj": fs, "node": node,
                            "scheduler": sched_name, "seed": seed,
                            "capture_silence": r["capture_silence"],
                            "outage_frac": r["outage_frac"],
                            "mean_soc": r["mean_soc"],
                        })
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    g = (df.groupby(["footsteps_per_visit", "footstep_mj", "node", "scheduler"])
           .agg(outage_mean=("outage_frac", "mean"),
                outage_sd=("outage_frac", "std"),
                capture_mean=("capture_silence", "mean"),
                capture_sd=("capture_silence", "std"))
           .reset_index())
    g["alive"] = g.outage_mean < ALIVE_OUTAGE
    return g


def plot(summary: pd.DataFrame, trace: str, out: Path):
    sched = "S4 forecast rate"
    fig, axes = plt.subplots(1, len(REAL_NODES), figsize=(4.2 * len(REAL_NODES), 3.8),
                             sharey=True)
    xs = np.log10(FOOTSTEP_MJ)
    for ax, node in zip(axes, REAL_NODES):
        s = summary[(summary.node == node) & (summary.scheduler == sched)]
        grid = (s.pivot(index="footsteps_per_visit", columns="footstep_mj",
                        values="outage_mean")
                  .reindex(index=FOOTSTEPS_PER_VISIT, columns=FOOTSTEP_MJ))
        ys = np.arange(len(FOOTSTEPS_PER_VISIT))
        # true log x-coordinates, so cells, contour and device lines agree
        im = ax.pcolormesh(xs, ys, grid.to_numpy(), shading="nearest", vmin=0, vmax=1,
                           cmap="RdYlGn_r")
        ax.contour(xs, ys, grid.to_numpy(), levels=[ALIVE_OUTAGE], colors="k",
                   linewidths=1.4)
        for mj, lbl in DEVICE_CLASSES:
            ax.axvline(np.log10(mj), color="tab:blue", ls=":", lw=1)
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{m:g}" for m in FOOTSTEP_MJ], fontsize=7, rotation=45)
        ax.set_yticks(range(len(FOOTSTEPS_PER_VISIT)))
        ax.set_yticklabels(FOOTSTEPS_PER_VISIT)
        ax.set_title(node, fontsize=10)
        ax.set_xlabel("footstep energy, mJ (log)")
    axes[0].set_ylabel("footsteps per visit")
    fig.colorbar(im, ax=axes, label="outage fraction (black line = alive boundary)",
                 shrink=0.85)
    fig.suptitle(f"{trace}, kinetic only (PV off), {sched}: "
                 "dotted = device classes", fontsize=10)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", nargs="?", default="hh104")
    ap.add_argument("--days", type=float, default=20)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--step", type=float, default=60)
    ap.add_argument("--lookahead", type=int, default=720)
    ap.add_argument("--pv", type=float, default=0.0,
                    help="indoor PV uW. 0 = kinetic only, the project's claim.")
    ap.add_argument("--visit", choices=["node", "zone"], default="node",
                    help="visit rule: node = lower bound on walkers, zone = upper bound")
    a = ap.parse_args()

    tag = f"{a.trace}_pv{a.pv:g}" + ("" if a.visit == "node" else f"_visit{a.visit}")
    df = sweep(a.trace, a.days, a.seeds, a.step, a.lookahead, a.pv, a.visit)
    raw_dir = REPORTS / f"sweep_{tag}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(raw_dir / "sweep.csv", index=False)

    summ = summarise(df)
    committed = REPORTS / "committed"
    committed.mkdir(parents=True, exist_ok=True)
    summ.round(4).to_csv(committed / f"sweep_{tag}.csv", index=False)
    plot(summ, a.trace, committed / f"sweep_{tag}.png")

    print(f"{tag}: {len(df)} runs -> {raw_dir}/sweep.csv")
    print(f"summary -> {committed}/sweep_{tag}.csv")
    for sched in summ.scheduler.unique():
        print(f"\n{sched}: lowest footstep (mJ) at which the node is alive "
              f"(outage < {ALIVE_OUTAGE}), by footsteps per visit")
        for node in REAL_NODES:
            s = summ[(summ.node == node) & (summ.scheduler == sched)]
            cells = []
            for fpv in FOOTSTEPS_PER_VISIT:
                alive = s[(s.footsteps_per_visit == fpv) & s.alive]
                cells.append(f"{fpv}:{alive.footstep_mj.min():g}"
                             if not alive.empty else f"{fpv}:never")
            print(f"  {node:<10} " + "  ".join(cells))


if __name__ == "__main__":
    main()
