"""
The closed loop: harvest -> store -> scheduler -> tasks -> metrics.

Primary metric is critical-event capture DURING SILENCE. Everything else is
reported but not optimised against.

Before quoting any result from this module, check two fields it returns:
`sustainable` and `run_long_enough`. A node whose harvest does not cover its
standing overhead is running down whatever charge it started with, and its
metrics describe that charge rather than the harvester. See initial_energy().
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from storage import storage_from_config, val
from schedulers import SilenceForecaster, OracleForecaster


def task_costs(cfg):
    t = cfg["tasks"]
    return {"sense": val(t["occ_sense_mj"]),
            "tx": val(t["tx_espnow_mj"]),
            "alarm": val(t["alarm_mj"]) + val(t["tx_espnow_mj"])}


def initial_energy(cfg, e_max_mj: float) -> float:
    """Starting charge, from sim.e_init_fraction in energy.yaml.

    This used to be hardcoded at 0.5 * e_max inside Storage, and the config key
    was dead. Harmless for a 440 mJ supercapacitor (220 mJ is a few hours of
    overhead) but it silently invalidates any large-storage experiment: with a
    60,000 mJ cell the node begins with 30,000 mJ of free energy and coasts for
    roughly 90 days. A 20-day run then reports capture = 1.000 for a door node
    harvesting 6.4 mJ/day against ~325 mJ/day of overhead -- that is the cell
    discharging, not the harvester working. The same node over 55 days collapses
    to 0.232 capture and 62 % outage.

    Default is 0.0: a node that was just installed has not harvested anything.
    Set the fraction deliberately if you want a pre-charged node, and say so
    when reporting.
    """
    node = cfg.get("sim", {}).get("e_init_fraction", {"value": 0.0})
    return float(val(node)) * e_max_mj


def run_node(node, harvest, events, cfg, sched_factory, timestep_s,
             crit_per_day=24.0, seed=0, warmup_frac=0.2, silence_win=30,
             interrupt_driven=True, forecaster="hist"):
    """
    interrupt_driven: a real harvesting node does NOT poll. It wakes on an
    interrupt from the harvest event itself, so paid sensing can only be
    attempted in a step where a detection actually occurred. Polling every
    step costs ~1440 senses/day (~5600 mJ) against a floor node harvesting
    ~1340 mJ/day -- the poll, not the policy, dominates the energy budget and
    makes "never wake up" win by construction. Set False only to reproduce
    the polled baseline.
    """
    h = harvest[node].to_numpy()
    ev = events[node].to_numpy()
    n = len(h)
    spd = int(86400 / timestep_s)

    store = storage_from_config(cfg, timestep_s)
    store.e = initial_energy(cfg, store.e_max_mj)
    e_init = store.e
    costs = task_costs(cfg)

    # "silence" = inflow below standing overhead, not inflow == 0
    warm = int(warmup_frac * n)
    if forecaster == "oracle":
        # UPPER BOUND ONLY. Sees the true future harvest, so S3/S4 results here
        # are the ceiling on what ANY forecaster can deliver. Never report an
        # oracle number as a method result.
        fc = OracleForecaster(h, starve_mj_per_step=store.overhead_per_step)
    else:
        fc = SilenceForecaster(spd, starve_mj_per_step=store.overhead_per_step)
        fc.fit(h[:warm])                  # train on the warmup span only
    sched = sched_factory(fc)

    rng = np.random.default_rng(seed)
    crit = rng.random(n) < (crit_per_day / spd)
    # a window is SILENT if nothing has arrived recently
    roll = pd.Series(h).rolling(silence_win, min_periods=1).mean().to_numpy()
    silent_now = roll <= store.overhead_per_step

    ctx = {"costs": costs}
    m = dict(sense=0, tx=0, reported=0, crit=0, crit_cap=0,
             crit_sil=0, crit_sil_cap=0, lat=[])
    pending, queue = 0, []
    soc_log, res_log = np.empty(n), np.empty(n)

    for t in range(n):
        store.add_harvest(h[t])
        counting = t >= warm

        # Tier 2 -- critical. Highest priority, ignores the reserve.
        if crit[t]:
            silent = bool(silent_now[t])
            if counting:
                m["crit"] += 1
                m["crit_sil"] += silent
            if store.spend(costs["alarm"]):
                if counting:
                    m["crit_cap"] += 1
                    m["crit_sil_cap"] += silent

        # Tier 0 -- harvest-coincident detection. Free: the transducer knows.
        if ev[t] > 0:
            pending += int(ev[t])
            queue.append(t)

        ctx["oldest"] = (t - queue[0]) if queue else 0
        r = sched.reserve(store, t, ctx)
        res_log[t] = r
        do_sense, do_tx = sched.decide(store, t, ctx)

        # Tier 1 -- paid sensing and transmission, accounted separately so a
        # batching policy is not undercounted.
        # Tier 1 paid sensing: only attemptable when the transducer has
        # actually woken the node (interrupt), not on a free-running timer.
        if interrupt_driven and ev[t] <= 0:
            do_sense = False
        if do_sense and store.spend(costs["sense"], r):
            if counting:
                m["sense"] += 1
        # NB: transmission uses the scheduler's TX floor, not the sensing
        # reserve. Applying r here silently starved delivery on sparse nodes.
        if do_tx and store.spend(costs["tx"], sched.tx_reserve(store, t, ctx)):
            if counting:
                m["tx"] += 1
                m["reported"] += pending
                m["lat"].extend(t - q for q in queue)
            pending, queue = 0, []

        store.apply_overhead()
        soc_log[t] = store.soc

    # Two windows, deliberately distinct. Rates that describe SCHEDULER
    # behaviour are counted after warmup, because the forecaster is still
    # being fitted before that. Storage totals (wasted, outage) accumulate
    # from step 0, so they must be divided by the FULL span -- dividing them
    # by the post-warmup span inflated wasted_mj_per_day by 1/(1-warmup_frac)
    # and produced the impossible result that a node wasted more than it
    # harvested.
    days = (n - warm) * timestep_s / 86400          # post-warmup, for rates
    days_all = n * timestep_s / 86400               # full span, for totals

    # --- is this result meaningful at all? ---------------------------------
    # A node only sustains itself if daily harvest covers standing overhead.
    # If it does not, it is spending its initial charge, and any run shorter
    # than the time needed to exhaust that charge measures the store rather
    # than the harvester. `burn_in_days` is how long the initial charge alone
    # would last at the current deficit; a run should be comfortably longer.
    harvest_per_day = float(h.sum()) / max(days_all, 1e-9)
    overhead_per_day = store.overhead_per_step * 86400.0 / timestep_s
    deficit = overhead_per_day - harvest_per_day
    burn_in_days = (e_init / deficit) if (deficit > 0 and e_init > 0) else 0.0

    return {
        "node": node, "scheduler": sched.name,
        "capture_silence": m["crit_sil_cap"] / max(1, m["crit_sil"]),
        "capture_all": m["crit_cap"] / max(1, m["crit"]),
        "n_crit_silence": m["crit_sil"],
        "reported_per_day": m["reported"] / max(days, 1e-9),
        "senses_per_day": m["sense"] / max(days, 1e-9),
        "mean_latency_min": (np.mean(m["lat"]) * timestep_s / 60) if m["lat"] else np.nan,
        "mean_soc": float(soc_log[warm:].mean()),
        "outage_frac": store.outage_steps / n,
        "wasted_mj_per_day": store.wasted / max(days_all, 1e-9),
        # Report the ledger split, not just what was captured. Energy that
        # enters the store and then leaks away is not useful, and summing the
        # two makes a leakier store look better than it is: a 3.6 F capacitor
        # captures ~13,000 mJ/day more than a 0.047 F one yet delivers LESS to
        # tasks (446 vs 512 mJ/day), because ~14,700 mJ/day leaks straight out.
        # `spent` is the only column that does any work.
        "leaked_mj_per_day": store.leaked / max(days_all, 1e-9),
        "spent_mj_per_day": store.spent / max(days_all, 1e-9),
        "harvest_mj_per_day": harvest_per_day,
        "overhead_mj_per_day": overhead_per_day,
        "sustainable": bool(harvest_per_day >= overhead_per_day),
        "burn_in_days": burn_in_days,
        "run_long_enough": bool(days_all >= 2 * burn_in_days),
        "balance_err": store.balance_error(e_init),
    }, soc_log, res_log