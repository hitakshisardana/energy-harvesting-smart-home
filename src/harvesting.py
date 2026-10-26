"""
THE BRIDGE: M2's event stream -> millijoules per node per timestep.

Until this file exists the data pipeline and the simulator cannot connect.

  events_<trace>.parquet          (one row per detected event)
        |  transduction multiplier  (a motion event is a PRESENCE detection,
        |                            not a footstep -- see energy.yaml S1)
        |  rectifier x PMIC efficiency
        v
  harvest[t, node]  in mJ         + indoor PV, which is occupancy-INDEPENDENT

Also owns the synthetic gym generator, conditioned on real occupancy so it
cannot produce exercise while the home is empty. Single copy -- silence_analysis
imports it from here rather than keeping its own.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from storage import val

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

REAL_NODES = ["floor", "furniture", "ambient", "door"]

# A PIR RE-TRIGGERS while someone is in the zone: the floor node fires ~32
# events in ~34 s, and 80% of its events arrive within 10 s of the previous
# one. Raw event count therefore measures PIR retrigger behaviour, i.e.
# PRESENCE -- not motion. Someone sitting still generates events and zero
# footsteps. Multiplying raw events by a footsteps-per-event factor
# overestimates floor harvest by roughly an order of magnitude.
#
# Fix: collapse events separated by less than DEBOUNCE_S into one VISIT, and
# apply the transduction multiplier to visits. "Footsteps per visit" is a
# property of people; "footsteps per detection" is a property of sensor
# firmware. Only the first is physically meaningful.
DEBOUNCE_S = 60.0
# The four kinetic sources. Every event harvests from exactly one of them.
#   footstep     floor and ambient motion (people walking)
#   compression  furniture: lounge-chair motion and bedroom sleep presence
#   door         door openings: one magnet flyby per event, not debounced
#   exercise     synthetic gym sessions (see synth_gym_counts)
SOURCES = ("footstep", "compression", "door")
SOURCE_RAW = {"footstep": "footstep", "compression": "compression", "door": "door_flyby"}
SOURCE_MULT = {"footstep": "footsteps_per_visit",
               "compression": "compressions_per_furniture_event",
               "door": "flybys_per_door_event"}


def source_of(node: str, etype: str) -> str:
    """Which kinetic source an event harvests from."""
    if etype == "door":
        return "door"
    if etype == "sleep_presence" or (node == "furniture" and etype == "motion"):
        return "compression"
    return "footstep"


def synth_gym_counts(grid, occupancy, timestep_s, cfg, seed=7):
    """Exercise sessions sampled ONLY from windows where the home is occupied."""
    rng = np.random.default_rng(seed)
    hours = grid.hour.to_numpy()
    prop = np.zeros(24)
    prop[6:9], prop[17:21], prop[9:17] = 0.35, 1.00, 0.08   # ASSUMPTION
    p = prop[hours] * np.asarray(occupancy, dtype=float)
    counts = np.zeros(len(grid))
    if p.sum() <= 0:
        return counts
    p = p / p.sum()
    g = cfg["nodes"]["gym"]
    n_days = len(grid) * timestep_s / 86400
    n_sessions = max(1, int(round(g["sessions_per_week"] * n_days / 7)))
    span = max(1, int(round(g["session_minutes"] * 60 / timestep_s)))
    starts = rng.choice(len(grid), size=min(n_sessions, len(grid)),
                        replace=False, p=p)
    for s in starts:
        counts[s:s + span] = 1.0 / span      # spread one session over its span
    return counts


def to_visits(ev: pd.DataFrame, debounce_s: float = DEBOUNCE_S,
              by: str = "node") -> pd.DataFrame:
    """
    Collapse sensor re-triggers into discrete visits.

    by="node": one stream per node, so the zones of a node are merged. Two
               people in different rooms of the same node count as one visit.
               LOWER bound on concurrent walkers.
    by="zone": one stream per zone, so overlapping rooms count as separate
               visits. UPPER bound: one person moving between two rooms within
               the debounce window is also counted twice.

    Door openings are never merged: each one is a single magnet flyby.
    """
    if by not in ("node", "zone"):
        raise ValueError(f"by must be 'node' or 'zone', got {by!r}")
    keys = ["node_id"] if by == "node" else ["node_id", "zone"]
    out = []
    for _, g in ev.groupby(keys, sort=False):
        g = g.sort_values("ts")
        gap = g.ts.diff().dt.total_seconds().fillna(1e9)
        out.append(g[(gap > debounce_s) | (g.event_type == "door")])
    return pd.concat(out).sort_values("ts") if out else ev.iloc[:0]


def build_harvest(trace: str, cfg: dict, timestep_s: float = 60,
                  footstep_mult: float | None = None,
                  pv_uw: float | None = None,
                  seed: int = 7, days: float | None = None,
                  debounce_s: float = DEBOUNCE_S,
                  raw_events: bool = False,
                  visit_by: str = "node"):
    """
    Returns (harvest_mj, events, index):
      harvest_mj : DataFrame  [step x node]  mJ arriving this step
      events     : DataFrame  [step x node]  raw detection count (Tier-0, free)
      index      : DatetimeIndex
    """
    path = PROC / f"events_{trace}.parquet"
    if not path.exists():
        raise SystemExit(f"missing {path} - run build_events.py first")
    ev_raw = pd.read_parquet(path)
    # Tier-0 detections the hub can receive are still per RAW event; the
    # HARVEST is per visit. Keep the two streams separate.
    ev = ev_raw if raw_events else to_visits(ev_raw, debounce_s, visit_by)

    freq = pd.Timedelta(seconds=timestep_s)
    ev["w"] = ev.ts.dt.floor(freq)
    start = ev.w.min()
    end = ev.w.max() if days is None else start + pd.Timedelta(days=days)
    grid = pd.date_range(start, end, freq=freq)

    tr, hv = cfg["transduction"], cfg["harvest"]
    eff = val(hv["efficiency"]["rectifier"]) * val(hv["efficiency"]["pmic"])
    if footstep_mult is None:
        footstep_mult = val(tr["footsteps_per_visit"])
    if pv_uw is None:
        pv_uw = val(hv["indoor_pv_uw"])

    mults = {"footstep": footstep_mult,   # per VISIT
             "compression": val(tr["compressions_per_furniture_event"]),
             "door": val(tr["flybys_per_door_event"])}

    events = pd.DataFrame(0.0, index=grid, columns=REAL_NODES + ["gym"])
    harvest = pd.DataFrame(0.0, index=grid, columns=REAL_NODES + ["gym"])

    counted = ev.groupby(["w", "node_id", "event_type"]).size()
    for (w, node, etype), n in counted.items():
        if w not in harvest.index or node not in REAL_NODES:
            continue
        events.at[w, node] += n
        src = source_of(node, etype)
        raw = val(hv["per_event_raw_mj"][SOURCE_RAW[src]])
        harvest.at[w, node] += n * mults[src] * raw * eff

    # --- synthetic gym, conditioned on real occupancy ---
    occ = (events[REAL_NODES].sum(axis=1) > 0).astype(float)
    gsess = synth_gym_counts(grid, occ, timestep_s, cfg, seed)
    gym_raw = val(hv["per_event_raw_mj"]["gym_session"])
    harvest["gym"] = gsess * gym_raw * eff
    events["gym"] = (gsess > 0).astype(float)

    # --- indoor PV: occupancy-INDEPENDENT, so it fills the silence ---
    hours = grid.hour + grid.minute / 60.0
    dl_h = val(hv["indoor_pv_uw"]["daylight_hours_per_day"])
    lo, hi = 12 - dl_h / 2, 12 + dl_h / 2
    daylight = np.clip(np.sin((hours - lo) / max(hi - lo, 1e-9) * np.pi), 0, None)
    pv_mj_step = pv_uw * 1e-6 * timestep_s * 1000.0
    for n in harvest.columns:
        harvest[n] = harvest[n] + pv_mj_step * daylight

    return harvest, events, grid