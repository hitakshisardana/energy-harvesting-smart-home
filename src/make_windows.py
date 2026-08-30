"""
Bin the per-node event stream into fixed time windows -> the table the
forecaster trains on.

  events_<name>.parquet   (one row per event)
        |
        v
  windows_<name>.parquet  (one row per node per 15-min window)
    window_start, node_id,
    n_events, n_motion, n_door, n_sleep_presence,   <- targets / inputs
    hour, dow, is_weekend,                           <- calendar features
    covered,                                         <- False inside logger-off gaps
    split  in {train, val, test}                     <- contiguous, by date (70/15/15)

Usage:
    python src/make_windows.py hh104
    python src/make_windows.py hh104 hh101 mn50 mn51 --freq 15min
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from casas_io import load

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"

EVENT_TYPES = ["motion", "door", "sleep_presence"]


def build_one(name: str, freq: str) -> pd.DataFrame | None:
    ev_path = PROC / f"events_{name}.parquet"
    raw_path = RAW / f"{name}.csv"
    if not ev_path.exists():
        print(f"!! missing {ev_path} - run build_events.py first")
        return None

    ev = pd.read_parquet(ev_path)
    ev["window_start"] = ev.ts.dt.floor(freq)
    grid = pd.date_range(ev.window_start.min(), ev.window_start.max(), freq=freq)

    # coverage: a window is "uncovered" only if it sits inside a long run of
    # windows with NO sensor activity anywhere in the home (logger was off).
    # Short quiet stretches - e.g. a still sleeper at 4am - stay covered.
    raw = load(raw_path)
    home_count = (raw.groupby(raw.ts.dt.floor(freq)).size()
                     .reindex(grid, fill_value=0))
    GAP_WINDOWS = max(1, int(pd.Timedelta("4h") / pd.Timedelta(freq)))
    is_zero = home_count.eq(0).to_numpy()
    covered_arr = np.ones(len(grid), dtype=bool)
    i = 0
    while i < len(is_zero):
        if is_zero[i]:
            j = i
            while j < len(is_zero) and is_zero[j]:
                j += 1
            if j - i > GAP_WINDOWS:
                covered_arr[i:j] = False
            i = j
        else:
            i += 1
    covered = pd.Series(covered_arr, index=grid, name="covered")

    # contiguous date split
    days = np.array(sorted({d.date() for d in grid}))
    n = len(days)
    split_of = {}
    for i, d in enumerate(days):
        split_of[d] = ("train" if i < 0.70 * n
                       else "val" if i < 0.85 * n
                       else "test")

    parts = []
    for node_id, g in ev.groupby("node_id"):
        counts = (g.groupby(["window_start", "event_type"]).size()
                    .unstack("event_type", fill_value=0)
                    .reindex(grid, fill_value=0)
                    .reindex(columns=EVENT_TYPES, fill_value=0))
        w = pd.DataFrame(index=grid)
        w["node_id"] = node_id
        w["n_events"] = counts.sum(axis=1).astype(int)
        for et in EVENT_TYPES:
            w[f"n_{et}"] = counts[et].astype(int)
        parts.append(w)

    win = pd.concat(parts).reset_index(names="window_start")
    win["hour"] = win.window_start.dt.hour
    win["dow"] = win.window_start.dt.dayofweek
    win["is_weekend"] = (win.dow >= 5).astype(int)
    win["covered"] = win.window_start.map(covered).astype(bool)
    win["split"] = win.window_start.dt.date.map(split_of)

    win = win.sort_values(["node_id", "window_start"]).reset_index(drop=True)
    dest = PROC / f"windows_{name}.parquet"
    win.to_parquet(dest, index=False)

    # ---- summary ----
    kept = win[win.covered]
    print(f"\n{name}: {len(win):,} rows  ({win.node_id.nunique()} nodes x "
          f"{len(grid):,} windows @ {freq})  ->  {dest.name}")
    print(f"  covered {kept.window_start.nunique():,}/{len(grid):,} windows "
          f"({len(grid) - kept.window_start.nunique()} in logger-off gaps)")
    by_split = (kept.groupby("split").window_start.nunique()
                    .reindex(["train", "val", "test"]))
    print(f"  windows per split: {by_split.to_dict()}")
    print("  mean n_events/window by node (covered only):")
    print(kept.groupby("node_id").n_events.mean().round(2).to_string()
              .replace("\n", "\n    ").rjust(4))

    # ---- overview plot ----
    daily = (kept.groupby(kept.window_start.dt.date).n_events.sum())
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(daily.index, daily.values, lw=0.9)
    for lbl in ("val", "test"):
        first = min(d for d, s in split_of.items() if s == lbl)
        ax.axvline(first, color="k", ls="--", lw=0.8)
        ax.text(first, ax.get_ylim()[1] * 0.92, f" {lbl}", fontsize=8)
    ax.set(title=f"{name}: total activity per day, with split boundaries",
           xlabel="date", ylabel="events/day")
    fig.autofmt_xdate(); fig.tight_layout()
    (REPORTS / f"windows_{name}").mkdir(parents=True, exist_ok=True)
    fig.savefig(REPORTS / f"windows_{name}" / "overview.png", dpi=110)
    plt.close(fig)
    return win


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    freq = "15min"
    if "--freq" in sys.argv:
        freq = sys.argv[sys.argv.index("--freq") + 1]
    for name in (args or ["hh104"]):
        build_one(name, freq)


if __name__ == "__main__":
    main()
