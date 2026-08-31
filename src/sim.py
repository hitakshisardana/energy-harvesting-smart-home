"""
The closed loop: harvest -> store -> scheduler -> tasks -> metrics.

Primary metric is critical-event capture DURING SILENCE. Everything else is
reported but not optimised against.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from storage import storage_from_config, val
from schedulers import SilenceForecaster


def task_costs(cfg):
    t = cfg["tasks"]
    return {"sense": val(t["occ_sense_mj"]),
            "tx": val(t["tx_espnow_mj"]),
            "alarm": val(t["alarm_mj"]) + val(t["tx_espnow_mj"])}


def run_node(node, harvest, events, cfg, sched_factory, timestep_s,
             crit_per_day=24.0, seed=0, warmup_frac=0.2, silence_win=30,
             interrupt_driven=True):
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
    e_init = store.e
    costs = task_costs(cfg)

    # "silence" = inflow below standing overhead, not inflow == 0
    fc = SilenceForecaster(spd, starve_mj_per_step=store.overhead_per_step)
    warm = int(warmup_frac * n)
    fc.fit(h[:warm])                      # train on the warmup span only
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

    days = (n - warm) * timestep_s / 86400
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
        "wasted_mj_per_day": store.wasted / max(days, 1e-9),
        "balance_err": store.balance_error(e_init),
    }, soc_log, res_log