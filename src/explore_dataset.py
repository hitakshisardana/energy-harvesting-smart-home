"""
Exploratory analysis of a CASAS testbed log (Phase 1, step 1).

Answers the Phase 0 questions we need before trusting the dataset:
  - how long is the trace, how many events, how many usable days
  - which zones exist, how active each one is
  - is there a real door-event stream (needed for the Door harvesting node)
  - what activity labels exist (sensing-utility ground truth)
  - how do events distribute over the hour of day (needed for the
    "energy vs demand time-offset" experiment in Section 24)
  - where are the gaps / dead stretches

Usage:
    python src/explore_dataset.py data/raw/hh101.csv
    python src/explore_dataset.py data/raw/hh101.csv data/raw/mn50.csv
Outputs: a printed report + PNGs and a JSON summary in reports/<name>/
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from casas_io import ON_STATES, OFF_STATES, load

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def classify(zone: str, state: str) -> str:
    """Rough sensor-type guess from the zone/sensor name."""
    z = zone.lower()
    if "door" in z or z.startswith("d0") or z.startswith("md"):
        return "door"
    if "light" in z or z.startswith("ls") or z.startswith("l0"):
        return "light"
    if state not in ON_STATES and state not in OFF_STATES:
        return "analog"          # temperature / battery / etc.
    if z.startswith("t0") or "temp" in z:
        return "analog"
    return "motion"


def explore(path: Path) -> dict:
    name = path.stem
    outdir = REPORTS / name
    outdir.mkdir(parents=True, exist_ok=True)
    df = load(path)
    if df.empty:
        print(f"!! {name}: no parseable rows")
        return {}

    df["kind"] = [classify(z, s) for z, s in zip(df.zone, df.state)]
    span = df.ts.max() - df.ts.min()
    days = span.total_seconds() / 86400
    on = df[df.state.isin(ON_STATES)]

    # per-zone activity
    zt = (df.groupby("zone")
            .agg(events=("ts", "size"),
                 kind=("kind", lambda s: s.mode().iat[0]),
                 first=("ts", "min"), last=("ts", "max"))
            .sort_values("events", ascending=False))

    # activity labels (begin markers only)
    labels = (df.loc[df.label.str.contains('begin', case=False, na=False), "label"]
                .str.split("=").str[0].str.strip().value_counts())

    # events per hour-of-day (ON events = presence/activity)
    by_hour = on.groupby(on.ts.dt.hour).size().reindex(range(24), fill_value=0)

    # daily event count -> find dead days
    by_day = df.groupby(df.ts.dt.date).size()
    dead_days = int((by_day < max(10, by_day.median() * 0.05)).sum())

    # largest inter-event gap
    gap = df.ts.diff().max()

    summary = {
        "file": str(path),
        "rows": int(len(df)),
        "start": str(df.ts.min()),
        "end": str(df.ts.max()),
        "span_days": round(days, 1),
        "calendar_days": int(len(by_day)),
        "dead_days": dead_days,
        "largest_gap_hours": round(gap.total_seconds() / 3600, 1),
        "n_zones": int(df.zone.nunique()),
        "kind_counts": df.kind.value_counts().to_dict(),
        "door_zones": sorted(df.loc[df.kind == "door", "zone"].unique().tolist()),
        "door_events": int((df.kind == "door").sum()),
        "motion_on_events": int(on[on.kind == "motion"].shape[0]),
        "activity_labels": labels.to_dict(),
        "top_zones": zt.head(15)["events"].to_dict(),
    }

    # ---- console report ----
    print(f"\n{'='*66}\n{name}   {path}\n{'='*66}")
    print(f"span            : {summary['start']}  ->  {summary['end']}")
    print(f"                  {summary['span_days']} days "
          f"({summary['calendar_days']} calendar days, "
          f"{dead_days} near-empty, largest gap {summary['largest_gap_hours']} h)")
    print(f"rows            : {summary['rows']:,}")
    print(f"zones           : {summary['n_zones']}   kinds: {summary['kind_counts']}")
    print(f"door stream     : {summary['door_events']:,} events "
          f"on {summary['door_zones']}")
    print(f"motion ON       : {summary['motion_on_events']:,}")
    print(f"activity labels : {summary['activity_labels']}")
    print("top zones by event count:")
    for z, c in list(summary["top_zones"].items())[:10]:
        print(f"    {z:<22} {c:>9,}  ({zt.loc[z,'kind']})")

    # ---- plots ----
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.bar(by_hour.index, by_hour.values, color="#2b6cb0")
    ax.set(title=f"{name}: motion ON events by hour of day",
           xlabel="hour", ylabel="events", xticks=range(0, 24, 2))
    fig.tight_layout(); fig.savefig(outdir / "events_by_hour.png", dpi=110); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(by_day.index, by_day.values, lw=0.9, color="#2b6cb0")
    ax.set(title=f"{name}: events per calendar day", xlabel="date", ylabel="events")
    fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(outdir / "events_by_day.png", dpi=110); plt.close(fig)

    topz = zt.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(topz.index, topz["events"], color="#2b6cb0")
    ax.set(title=f"{name}: top zones by event count", xlabel="events")
    fig.tight_layout(); fig.savefig(outdir / "top_zones.png", dpi=110); plt.close(fig)

    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"-> reports/{name}/  (3 PNGs + summary.json)")
    return summary


def main():
    paths = [Path(p) for p in sys.argv[1:]] or [ROOT / "data/raw/hh101.csv"]
    allsum = {}
    for p in paths:
        if not p.exists():
            print(f"!! missing {p}")
            continue
        allsum[p.stem] = explore(p)
    (REPORTS / "index.json").write_text(json.dumps(allsum, indent=2))


if __name__ == "__main__":
    main()
