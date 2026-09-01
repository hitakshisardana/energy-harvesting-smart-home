"""
THE GO / NO-GO EXPERIMENT for the forecasting track.

Question: before spending a week training an LSTM, how much can ANY forecaster
possibly buy us?

Method: run the forecast-driven schedulers (S3, S4) twice on identical traces,
seeds and energy config -- once with the histogram forecaster already in the
repo, once with `OracleForecaster`, which reads the TRUE future harvest. The
oracle cannot be built in reality; it is the ceiling. A trained model lives
somewhere between the two columns and can never pass the oracle.

Also runs S1 (greedy, no forecast) as the floor, so the gap that forecasting is
competing for is visible: floor -> histogram -> oracle.

Read it as a decision:
  oracle - histogram is small  -> prediction is NOT the bottleneck. The energy
                                  budget is. Do not train a model to close a
                                  gap that does not exist; report the finding.
  oracle - histogram is large  -> headroom is real, and its size is the budget
                                  for the ML track.

    python src/oracle_bound.py hh104
    python src/oracle_bound.py hh104 --pv 0 --days 20 --seeds 5
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from storage import load_config
from harvesting import build_harvest, REAL_NODES, DEBOUNCE_S
from schedulers import S1_Reactive, S3_ForecastReserve, S4_ForecastRate
from sim import run_node

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

METRICS = ["capture_silence", "reported_per_day", "mean_latency_min",
           "outage_frac", "mean_soc"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", nargs="?", default="hh104")
    ap.add_argument("--days", type=float, default=20)
    ap.add_argument("--step", type=float, default=60)
    ap.add_argument("--mult", type=float, default=None)
    ap.add_argument("--pv", type=float, default=None,
                    help="indoor PV uW. Use 0 for the kinetic-only system the "
                         "project actually claims to build.")
    ap.add_argument("--lookahead", type=int, default=720)
    ap.add_argument("--seeds", type=int, default=5,
                    help="critical events are injected at random; a single "
                         "seed cannot distinguish a result from noise")
    ap.add_argument("--debounce", type=float, default=None)
    a = ap.parse_args()

    cfg = load_config()
    harvest, events, grid = build_harvest(a.trace, cfg, a.step, a.mult, a.pv,
                                          7, a.days, a.debounce or DEBOUNCE_S)
    nodes = REAL_NODES + ["gym"]

    per_day = harvest.sum() / (len(grid) * a.step / 86400)
    print(f"{a.trace}: {len(grid):,} steps @ {a.step:g}s "
          f"({len(grid)*a.step/86400:.1f} days), pv={a.pv}, seeds={a.seeds}")
    print("\nmJ/day by node:")
    print(per_day.round(1).to_string())

    arms = [
        ("S1 reactive",          "hist",   lambda fc: S1_Reactive()),
        ("S3 forecast reserve",  "hist",   lambda fc: S3_ForecastReserve(fc, 1.5, a.lookahead)),
        ("S3 forecast reserve",  "oracle", lambda fc: S3_ForecastReserve(fc, 1.5, a.lookahead)),
        ("S4 forecast rate",     "hist",   lambda fc: S4_ForecastRate(fc, 1.5, a.lookahead)),
        ("S4 forecast rate",     "oracle", lambda fc: S4_ForecastRate(fc, 1.5, a.lookahead)),
    ]

    rows = []
    for node in nodes:
        for sched_name, mode, fac in arms:
            for seed in range(a.seeds):
                r, _, _ = run_node(node, harvest, events, cfg, fac, a.step,
                                   seed=seed, forecaster=mode)
                rows.append({"node": node, "scheduler": sched_name,
                             "forecast": mode, "seed": seed,
                             **{k: r[k] for k in METRICS}})
    df = pd.DataFrame(rows)

    outdir = REPORTS / f"oracle_{a.trace}"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "raw.csv", index=False)

    agg = (df.groupby(["node", "scheduler", "forecast"])
             .agg(["mean", "std"])[METRICS])

    def table(metric, nd=3):
        m = agg[(metric, "mean")].unstack(["scheduler", "forecast"])
        s = agg[(metric, "std")].unstack(["scheduler", "forecast"])
        return m.round(nd), s.round(nd)

    print(f"\n{'='*78}\nCAPTURE DURING SILENCE  (primary metric, mean over "
          f"{a.seeds} seeds)\n{'='*78}")
    m, s = table("capture_silence")
    print(m.to_string())
    print("\n  std across seeds:")
    print(s.to_string())

    # --- the number the whole ML decision hangs on ---
    print(f"\n{'='*78}\nHEADROOM: what perfect forecasting is worth\n{'='*78}")
    print("  A trained model lands somewhere in [histogram, oracle]. If that")
    print("  interval is inside the seed noise, the model cannot pay for itself.\n")
    hdr = f"  {'node':<11}{'sched':<22}{'hist':>8}{'oracle':>9}{'gain':>8}{'seed sd':>9}  verdict"
    print(hdr)
    for node in nodes:
        for sched_name in ["S3 forecast reserve", "S4 forecast rate"]:
            sub = df[(df.node == node) & (df.scheduler == sched_name)]
            h = sub[sub.forecast == "hist"].capture_silence
            o = sub[sub.forecast == "oracle"].capture_silence
            if h.empty or o.empty:
                continue
            gain = o.mean() - h.mean()
            sd = float(np.sqrt(h.var(ddof=1) + o.var(ddof=1))) if a.seeds > 1 else np.nan
            verdict = ("NO HEADROOM" if not np.isfinite(sd) or gain <= sd
                       else "headroom")
            print(f"  {node:<11}{sched_name:<22}{h.mean():>8.3f}{o.mean():>9.3f}"
                  f"{gain:>+8.3f}{sd:>9.3f}  {verdict}")

    print(f"\n{'='*78}\nINFORMATION LATENCY (min) -- the cost side\n{'='*78}")
    m, _ = table("mean_latency_min", 1)
    print(m.to_string())
    print(f"\n{'='*78}\nOUTAGE FRACTION\n{'='*78}")
    m, _ = table("outage_frac")
    print(m.to_string())

    print(f"\nWrote {outdir}/raw.csv")


if __name__ == "__main__":
    main()
