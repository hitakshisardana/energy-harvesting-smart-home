"""
Validate the event->visit debounce against an INDEPENDENT ground truth.

Why this exists
---------------
Harvest is proportional to physical motion, but a PIR RE-TRIGGERS while someone
is merely present: on hh104 the floor node fires ~32 events in a ~34 s visit,
and 80% of its events arrive within 10 s of the previous one. Multiplying raw
event count by a footsteps-per-event factor therefore measures sensor firmware
behaviour, not human motion, and overestimated floor harvest by ~10x.

harvesting.to_visits() collapses re-triggers into visits using DEBOUNCE_S. That
window was chosen by eye. This script checks it against something outside our
own modelling assumptions: the human-annotated activity labels already present
in the raw CASAS log (Cook_Breakfast="begin" ... "end").

The test
--------
Labelled activity episodes are discrete things a person did. Debounced visits
are discrete things we claim generated harvest. They should be the same ORDER
OF MAGNITUDE. If visits are 30x the labelled episodes, the debounce is still
counting re-triggers. If visits are far FEWER, it has merged distinct events.

This is a calibration check, not a fit. We are not tuning DEBOUNCE_S to make a
scheduler win -- we are asking whether the visit count is physically plausible.

    python src/validate_debounce.py hh104
    python src/validate_debounce.py hh104 hh101 --windows 15 30 60 120 300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from casas_io import load                      # noqa: E402
from harvesting import to_visits, DEBOUNCE_S   # noqa: E402

RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"

# a person takes roughly this many steps a day in total, across the whole home
STEPS_PER_DAY_TYPICAL = 5000


def labelled_episodes(trace: str) -> pd.DataFrame:
    """Human-annotated activity episodes: one row per label='begin'."""
    df = load(RAW / f"{trace}.csv")
    lab = df[df.label.str.contains("=", na=False)].copy()
    if lab.empty:
        return lab.assign(activity=[], phase=[])
    parts = lab.label.str.split("=", n=1, expand=True)
    lab["activity"] = parts[0].str.strip()
    lab["phase"] = parts[1].str.strip().str.strip('"').str.lower()
    return lab[lab.phase.str.startswith("begin")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="*", default=["hh104"])
    ap.add_argument("--windows", type=float, nargs="*",
                    default=[15, 30, 60, 120, 300])
    a = ap.parse_args()

    all_rows = []
    for trace in a.traces:
        ev = pd.read_parquet(PROC / f"events_{trace}.parquet")
        days = (ev.ts.max() - ev.ts.min()).total_seconds() / 86400
        lab = labelled_episodes(trace)

        print("=" * 74)
        print(f"{trace}  ({days:.1f} days)")
        print("=" * 74)

        if lab.empty:
            print("  no activity labels in this log -- external check unavailable")
            lab_per_day = float("nan")
        else:
            lab_per_day = len(lab) / days
            top = lab.activity.value_counts().head(6)
            print(f"  human-labelled activity episodes: {len(lab):,} "
                  f"({lab_per_day:.1f}/day, {lab.activity.nunique()} distinct)")
            print("  most frequent: "
                  + ", ".join(f"{k} {v}" for k, v in top.items()))

        print(f"\n  raw events/day by node:")
        raw_pd = ev.groupby("node_id").size() / days
        print("    " + "  ".join(f"{k} {v:.0f}" for k, v in raw_pd.items()))

        print(f"\n  visits/day as the debounce window varies "
              f"(current DEBOUNCE_S={DEBOUNCE_S:g}s):")
        hdr = "    window(s)  " + "".join(f"{n:>12s}" for n in sorted(raw_pd.index))
        print(hdr)
        for w in a.windows:
            v = to_visits(ev, w)
            per_day = v.groupby("node_id").size() / days
            mark = "  <- current" if w == DEBOUNCE_S else ""
            print("    " + f"{w:<10g}"
                  + "".join(f"{per_day.get(n, 0):12.1f}" for n in sorted(raw_pd.index))
                  + mark)
            for n in sorted(raw_pd.index):
                all_rows.append({"trace": trace, "window_s": w, "node": n,
                                 "visits_per_day": float(per_day.get(n, 0)),
                                 "raw_events_per_day": float(raw_pd[n]),
                                 "labelled_episodes_per_day": lab_per_day})

        # --- the actual check -------------------------------------------------
        print(f"\n  RATIO to human-labelled episodes ({lab_per_day:.1f}/day)")
        print("  A ratio near 1 means visits track annotated activity. A ratio in")
        print("  the tens means we are still counting sensor re-triggers.")
        print(hdr.replace("window(s)", "window(s)"))
        for w in a.windows:
            v = to_visits(ev, w)
            per_day = v.groupby("node_id").size() / days
            print("    " + f"{w:<10g}"
                  + "".join(f"{per_day.get(n, 0) / lab_per_day:12.2f}"
                            for n in sorted(raw_pd.index)))

        # --- physical plausibility -------------------------------------------
        print(f"\n  PHYSICAL PLAUSIBILITY (floor node)")
        print(f"  A resident takes ~{STEPS_PER_DAY_TYPICAL:,} steps/day across the whole")
        print("  home, so one zone should see a modest fraction of that. Implied")
        print("  steps/day on the floor zone, at 20 footsteps per visit:")
        for w in a.windows:
            v = to_visits(ev, w)
            fl = (v.node_id == "floor").sum() / days
            implied = fl * 20
            flag = "IMPLAUSIBLE - exceeds whole-body step count" \
                if implied > STEPS_PER_DAY_TYPICAL else "plausible"
            print(f"    window {w:<5g}s  {fl:6.1f} visits/day"
                  f"  -> {implied:7.0f} steps/day   {flag}")
        old = raw_pd.get("floor", 0) * 5
        print(f"\n    for comparison, the OLD per-raw-event model implied "
              f"{old:,.0f} steps/day")
        print(f"    on one tile -- more than the resident's entire daily step count.")
        print()

    if all_rows:
        REPORTS.mkdir(exist_ok=True)
        out = REPORTS / "debounce_validation.csv"
        pd.DataFrame(all_rows).to_csv(out, index=False)
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()