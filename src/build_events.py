"""
Turn a raw CASAS testbed log into the normalized per-node event stream that the
simulator and the forecaster both consume.

  raw:  2011-06-16,07:03:11.441,Kitchen,ON,Cook_Breakfast="begin"
  out:  ts=2011-06-16 07:03:11.441  node_id=floor  event_type=motion  zone=Kitchen

The room -> node mapping lives in config/nodes.yaml (frozen in Phase 0).
"gym" has no CASAS source and is synthesized elsewhere.

Usage:
    python src/build_events.py hh104
    python src/build_events.py hh104 hh101 mn50 mn51
Output: data/processed/events_<name>.parquet  + a printed summary
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml

from casas_io import ON_STATES, load

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
CFG = ROOT / "config" / "nodes.yaml"


def sleep_spans(df: pd.DataFrame) -> list[tuple]:
    """[(start, end), ...] for every Sleep-labelled interval in the log."""
    spans, start = [], None
    for ts, lab in zip(df.ts, df.label):
        low = lab.lower()
        if low.startswith("sleep=") and "begin" in low:
            start = ts
        elif low.startswith("sleep=") and "end" in low and start is not None:
            spans.append((start, ts))
            start = None
    return spans


def in_any_span(ts: pd.Series, spans: list[tuple]) -> pd.Series:
    mask = pd.Series(False, index=ts.index)
    for a, b in spans:
        mask |= (ts >= a) & (ts <= b)
    return mask


def _rows(sub: pd.DataFrame, node_id: str, etype: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": sub.ts.to_numpy(),
        "node_id": node_id,
        "event_type": etype,
        "zone": sub.zone.to_numpy(),
    })


def build_one(name: str, cfg: dict) -> pd.DataFrame | None:
    path = RAW / f"{name}.csv"
    if not path.exists():
        print(f"!! missing {path}")
        return None
    mapping = cfg["testbeds"].get(name)
    if mapping is None:
        print(f"!! no mapping for '{name}' in {CFG.name}")
        return None

    df = load(path)
    on = df[df.state.isin(ON_STATES)]
    needs_sleep = any(
        "motion_during_sleep" in rule
        for rule in mapping.values() if isinstance(rule, dict)
    )
    spans = sleep_spans(df) if needs_sleep else []

    frames = []
    for node_id, rule in mapping.items():
        if not isinstance(rule, dict):
            continue
        for zone in rule.get("motion", []):
            frames.append(_rows(on[on.zone == zone], node_id, "motion"))
        for zone in rule.get("door", []):
            frames.append(_rows(on[on.zone == zone], node_id, "door"))
        for zone in rule.get("motion_during_sleep", []):
            z = on[on.zone == zone]
            z = z[in_any_span(z.ts, spans)]
            frames.append(_rows(z, node_id, "sleep_presence"))

    ev = (pd.concat(frames, ignore_index=True)
            .sort_values("ts")
            .reset_index(drop=True))

    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"events_{name}.parquet"
    ev.to_parquet(dest, index=False)

    # ---- summary ----
    days = (ev.ts.max() - ev.ts.min()).total_seconds() / 86400
    print(f"\n{name}: {len(ev):,} events over {days:.1f} days  ->  {dest.name}")
    piv = ev.pivot_table(index="node_id", columns="event_type",
                         values="ts", aggfunc="size", fill_value=0)
    print(piv.to_string())
    per_day = (ev.groupby([ev.ts.dt.date, "node_id"]).size()
                 .groupby("node_id").mean().round().astype(int))
    print("mean events/day:", per_day.to_dict())
    missing = [n for n in cfg["nodes"]
              if n not in ev.node_id.unique() and n != "gym"]
    if missing:
        print(f"note: no events for {missing} in this testbed")

    # ---- one plot: event rate by hour of day, per node ----
    by_hour = (ev.groupby([ev.ts.dt.hour, "node_id"]).size()
                 .unstack("node_id", fill_value=0)
                 .reindex(range(24), fill_value=0))
    ndays = ev.ts.dt.normalize().nunique()
    fig, ax = plt.subplots(figsize=(9, 3.4))
    (by_hour / ndays).plot(ax=ax, marker="o", ms=3)
    ax.set(title=f"{name}: events per hour of day, per node (daily mean)",
           xlabel="hour", ylabel="events", xticks=range(0, 24, 2))
    ax.legend(title="node", fontsize=8)
    fig.tight_layout()
    REPORTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(REPORTS / f"events_{name}_by_hour.png", dpi=110)
    plt.close(fig)
    return ev


def main():
    names = sys.argv[1:] or ["hh104"]
    cfg = yaml.safe_load(CFG.read_text())
    for n in names:
        build_one(n, cfg)


if __name__ == "__main__":
    main()
