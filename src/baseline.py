"""
Naive forecasting baselines on the test split - the bar the LSTM/GRU must beat
(plan Section 11: "comparison with a persistence/naive baseline").

Methods, per node, predicting n_events for each 15-min window:
  persist_last    y_hat[t] = y[t-1]
  persist_daily   y_hat[t] = y[t - 1 day]        (same clock time yesterday)
  persist_weekly  y_hat[t] = y[t - 7 days]
  hour_mean       y_hat[t] = mean events at this node & hour over the TRAIN split

Reported: MAE, RMSE, and - framing it as occupancy detection (events > 0) -
accuracy and F1. Only covered windows are scored.

Usage:
    python src/baseline.py hh104
    python src/baseline.py hh104 hh101 mn50 mn51
Output: reports/baseline_<name>/metrics.json  + a predicted-vs-actual plot
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import f1_score

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"


def evaluate(name: str) -> dict | None:
    path = PROC / f"windows_{name}.parquet"
    if not path.exists():
        print(f"!! missing {path} - run make_windows.py first")
        return None
    w = pd.read_parquet(path).sort_values(["node_id", "window_start"])

    step = w.window_start.drop_duplicates().sort_values().diff().median()
    per_day = int(pd.Timedelta("1D") / step)

    rows, plot_data = [], {}
    for node, g in w.groupby("node_id"):
        g = g.set_index("window_start")
        y = g.n_events.where(g.covered)            # NaN inside logger-off gaps
        hour_mean = (g[g.split == "train"].groupby("hour").n_events.mean())
        preds = {
            "persist_last":   y.shift(1),
            "persist_daily":  y.shift(per_day),
            "persist_weekly": y.shift(7 * per_day),
            "hour_mean":      g.hour.map(hour_mean),
        }
        test = g.split.eq("test")
        for method, p in preds.items():
            m = test & y.notna() & p.notna()
            if m.sum() < 20:
                continue
            err = y[m] - p[m]
            yt, pt = (y[m] > 0).astype(int), (p[m] > 0).astype(int)
            rows.append({
                "node": node, "method": method, "n": int(m.sum()),
                "mae": round(float(err.abs().mean()), 3),
                "rmse": round(float(np.sqrt((err ** 2).mean())), 3),
                "occ_acc": round(float((yt == pt).mean()), 3),
                "occ_f1": round(float(f1_score(yt, pt, zero_division=0)), 3),
            })
        plot_data[node] = (y, preds["persist_last"], preds["persist_daily"], test)

    res = pd.DataFrame(rows)
    overall = (res.groupby("method")
                  .apply(lambda d: pd.Series({
                      "mae": np.average(d.mae, weights=d.n).round(3),
                      "rmse": np.average(d.rmse, weights=d.n).round(3),
                      "occ_acc": np.average(d.occ_acc, weights=d.n).round(3),
                      "occ_f1": np.average(d.occ_f1, weights=d.n).round(3),
                  }), include_groups=False)
                  .sort_values("mae"))

    outdir = REPORTS / f"baseline_{name}"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics.json").write_text(json.dumps({
        "trace": name,
        "per_node": rows,
        "overall": overall.reset_index().to_dict("records"),
    }, indent=2))

    print(f"\n{name}  (test split, covered windows only)")
    print("per-node MAE:")
    print(res.pivot(index="node", columns="method", values="mae").to_string())
    print("\noverall (event-count MAE/RMSE, occupancy acc/F1):")
    print(overall.to_string())

    # ---- plot: floor node, first ~3 test days ----
    node = "floor" if "floor" in plot_data else next(iter(plot_data))
    y, p_last, p_daily, test = plot_data[node]
    idx = y.index[test][: 3 * per_day]
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(idx, y.loc[idx], label="actual", lw=1.4)
    ax.plot(idx, p_last.loc[idx], label="persist_last", lw=0.9, alpha=.8)
    ax.plot(idx, p_daily.loc[idx], label="persist_daily", lw=0.9, alpha=.8)
    ax.set(title=f"{name} / {node}: actual vs naive baselines (first 3 test days)",
           xlabel="time", ylabel="events / 15 min")
    ax.legend(fontsize=8); fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(outdir / "pred_vs_actual.png", dpi=110)
    plt.close(fig)
    return {"overall": overall}


def main():
    for name in (sys.argv[1:] or ["hh104"]):
        evaluate(name)


if __name__ == "__main__":
    main()
