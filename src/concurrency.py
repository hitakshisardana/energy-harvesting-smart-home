"""
Concurrent walkers: how much the visit-merging rule changes footstep energy.

Two rules for turning motion events into visits (see harvesting.to_visits):
  by="node"  zones of a node merged into one stream  -> LOWER bound
  by="zone"  one stream per zone                      -> UPPER bound

The true number of simultaneous walkers lies between the two. CASAS cannot
say who is moving, so neither rule is the answer; the report should give the
range. Kinetic only (indoor PV off), full trace, footstep at the derived
value, 20 footsteps per visit.

Usage
    python src/concurrency.py
Output
    reports/committed/concurrency.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from storage import load_config
from harvesting import build_harvest

ROOT = Path(__file__).resolve().parents[1]
HOMES = ["hh104", "hh101", "hh107", "mn50", "mn51"]
RESIDENTS = {"hh104": 1, "hh101": 1, "hh107": 2, "mn50": 2, "mn51": 3}   # 3 = more than 2


def main():
    cfg = load_config()
    rows = []
    for home in HOMES:
        row = {"home": home, "residents": RESIDENTS[home]}
        for by in ("node", "zone"):
            H, E, G = build_harvest(home, cfg, 60, None, 0.0, 7, None, 60.0, False, by)
            days = len(G) * 60 / 86400
            row[f"floor_visits_per_day_{by}"] = round(E["floor"].sum() / days, 1)
            row[f"floor_mJ_per_day_{by}"] = round(H["floor"].sum() / days, 1)
            row[f"ambient_mJ_per_day_{by}"] = round(H["ambient"].sum() / days, 1)
        row["floor_ratio_zone_over_node"] = round(
            row["floor_mJ_per_day_zone"] / row["floor_mJ_per_day_node"], 2)
        rows.append(row)
        print(f"{home}: floor {row['floor_mJ_per_day_node']} (node) to "
              f"{row['floor_mJ_per_day_zone']} (zone) mJ/day, "
              f"ratio {row['floor_ratio_zone_over_node']}")

    out = pd.DataFrame(rows)
    committed = ROOT / "reports" / "committed"
    committed.mkdir(parents=True, exist_ok=True)
    out.to_csv(committed / "concurrency.csv", index=False)
    print(f"\nwrote {committed / 'concurrency.csv'}")


if __name__ == "__main__":
    main()
