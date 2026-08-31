"""
Schedulers. Every one is the same shape, so RL and MILP slot in unchanged.

    decide(store, t, ctx) -> (do_sense, do_transmit)
    reserve(store, t, ctx) -> mJ withheld from routine tasks

Two bugs cost real debugging time and are fixed here. Keep them fixed:
  1. A reserve raised only once silence has STARTED is useless -- by then the
     node has already spent its store. The policy must hoard BEFORE the silence,
     which requires looking ahead (see SilenceForecaster.upcoming_silence).
  2. Sensing must budget for the transmission it implies. Otherwise sensing
     drains to the reserve floor, transmission can never afford to fire, and
     the detection queue grows forever with nothing reaching the hub.
  3. Sensing and transmission need SEPARATE reserve floors. Gating both on the
     same silence reserve means hoarding also blocks delivery of detections
     the transducer already captured for free -- latency explodes into the
     hundreds of minutes and the policy fails a freshness constraint for a
     reason that has nothing to do with reserving. Transmission is the
     cheapest information-per-joule action available, so it protects only the
     alarm, not the full silence reserve.
"""
from __future__ import annotations

import numpy as np


class Scheduler:
    name = "base"

    def reserve(self, store, t, ctx):
        """Energy withheld from routine SENSING."""
        return 0.0

    def tx_reserve(self, store, t, ctx):
        """
        Energy withheld from TRANSMISSION -- separate from the sensing reserve.

        Transmission delivers detections the transducer already captured for
        free, so it is the cheapest information-per-joule action available and
        should protect only the alarm. Gating it at the full silence reserve
        starves delivery: on a sparse node the reserve can sit far above the
        stored energy, the queue never flushes, and the node reports NOTHING
        while still looking healthy on capture.
        """
        return self.reserve(store, t, ctx)

    def decide(self, store, t, ctx):
        raise NotImplementedError


class S0_FixedDuty(Scheduler):
    name = "S0 fixed duty"

    def __init__(self, period_steps=15):
        self.period = period_steps

    def decide(self, store, t, ctx):
        due = (t % self.period == 0)
        return due, due


class S1_Reactive(Scheduler):
    """Spend whenever affordable. No reserve. The greedy policy."""
    name = "S1 reactive"

    def decide(self, store, t, ctx):
        c = ctx["costs"]
        ok = store.can_afford(c["sense"] + c["tx"])
        return ok, ok


class S2_FixedReserve(Scheduler):
    """Constant reserve. Tune it properly or the S2/S3 comparison is a strawman."""
    name = "S2 fixed reserve"

    def __init__(self, frac=0.25):
        self.frac = frac

    def reserve(self, store, t, ctx):
        return self.frac * store.e_max_mj

    def tx_reserve(self, store, t, ctx):
        return min(self.reserve(store, t, ctx), ctx["costs"]["alarm"])

    def decide(self, store, t, ctx):
        c, r = ctx["costs"], self.reserve(store, t, ctx)
        sense = store.can_afford(c["sense"] + c["tx"], r)
        tx = ctx.get("oldest", 0) > 0 and store.can_afford(c["tx"],
                                                           self.tx_reserve(store, t, ctx))
        return sense, tx


class SilenceForecaster:
    """
    Time-of-day profile of P(harvesting). Deliberately a persistence-class
    baseline -- this is the bar M2's LSTM has to beat. Swap
    `upcoming_silence` for the model output and nothing else changes.
    """

    def __init__(self, steps_per_day: int, starve_mj_per_step: float = 0.0):
        self.spd = int(steps_per_day)
        self.hit = np.zeros(self.spd)
        self.n = np.zeros(self.spd)
        self._cache = {}
        # A node is STARVING, not harvesting, when inflow is below what standing
        # overhead consumes. With indoor PV the raw harvest is never exactly
        # zero in daylight, so a "harvest > 0" test makes the forecaster believe
        # silence never arrives and the reserve stays pinned at its floor.
        # Silence must be defined relative to overhead, not to zero.
        self.starve = float(starve_mj_per_step)

    def fit(self, harvest: np.ndarray):
        for i in range(len(harvest)):
            k = i % self.spd
            self.n[k] += 1
            self.hit[k] += harvest[i] > self.starve
        self._cache.clear()

    def p_harvest(self, t):
        k = int(t) % self.spd
        return 0.5 if self.n[k] == 0 else self.hit[k] / self.n[k]

    def upcoming_silence(self, t, lookahead, thresh=0.15):
        """Length (steps) of the next no-harvest run beginning within lookahead."""
        key = (int(t) % self.spd, lookahead)
        if key in self._cache:
            return self._cache[key]
        k = 0
        while k < lookahead and self.p_harvest(t + k) > thresh:
            k += 1
        if k >= lookahead:
            self._cache[key] = 0
            return 0
        d = 0
        while d < self.spd and self.p_harvest(t + k + d) <= thresh:
            d += 1
        self._cache[key] = d
        return d


class S3_ForecastReserve(Scheduler):
    """Reserve sized from the silence AHEAD. The proposed method."""
    name = "S3 forecast reserve"

    def __init__(self, fc: SilenceForecaster, safety=1.5, lookahead=720):
        self.fc, self.safety, self.lookahead = fc, safety, lookahead

    def reserve(self, store, t, ctx):
        c = ctx["costs"]
        silence = self.fc.upcoming_silence(t, self.lookahead)
        r = store.overhead_per_step * silence + c["alarm"] * self.safety
        return float(min(r, store.e_max_mj))

    def tx_reserve(self, store, t, ctx):
        return min(self.reserve(store, t, ctx), ctx["costs"]["alarm"])

    def decide(self, store, t, ctx):
        c, r = ctx["costs"], self.reserve(store, t, ctx)
        sense = store.can_afford(c["sense"] + c["tx"], r)
        tx = ctx.get("oldest", 0) > 0 and store.can_afford(c["tx"],
                                                           self.tx_reserve(store, t, ctx))
        return sense, tx


class S4_ForecastRate(Scheduler):
    """
    Forecast-driven RATE control -- the fair comparison against S0.

    S0 wins by controlling how OFTEN the node wakes. S2/S3 only control a
    threshold: they withhold energy but still attempt at every opportunity.
    Those are different action spaces, and giving the forecast only the
    threshold lever was an asymmetry in the original design, not a finding.

    S4 has S3's information (predicted silence) and S0's actuator (the sensing
    period). It budgets: of the energy on hand, set aside what the predicted
    silence will drain plus the alarm reserve, then spread whatever is left
    over the horizon as a sensing period.

        need   = overhead * D + alarm * safety
        spare  = max(0, E - need)
        n      = spare / (sense + tx)            # senses affordable
        period = (lookahead + D) / n             # spread them out

    Predicted long silence -> fewer affordable senses -> longer period.
    """
    name = "S4 forecast rate"

    def __init__(self, fc: SilenceForecaster, safety=1.5, lookahead=720,
                 min_period=1, max_period=1440):
        self.fc, self.safety, self.lookahead = fc, safety, lookahead
        self.min_period, self.max_period = min_period, max_period
        self.last_sense = -10**9
        self.period = float(min_period)

    def reserve(self, store, t, ctx):
        c = ctx["costs"]
        D = self.fc.upcoming_silence(t, self.lookahead)
        return float(min(store.overhead_per_step * D + c["alarm"] * self.safety,
                         store.e_max_mj))

    def decide(self, store, t, ctx):
        c = ctx["costs"]
        D = self.fc.upcoming_silence(t, self.lookahead)
        need = store.overhead_per_step * D + c["alarm"] * self.safety
        spare = max(0.0, store.e - need)
        n = spare / (c["sense"] + c["tx"])
        horizon = self.lookahead + D
        self.period = float(np.clip(horizon / max(n, 1e-9),
                                    self.min_period, self.max_period))

        due = (t - self.last_sense) >= self.period
        sense = due and store.can_afford(c["sense"] + c["tx"], c["alarm"])
        if sense:
            self.last_sense = t
        # transmission keeps its own lower floor (see note 3 above)
        tx = ctx.get("oldest", 0) > 0 and store.can_afford(c["tx"], c["alarm"])
        return sense, tx

    def tx_reserve(self, store, t, ctx):
        return ctx["costs"]["alarm"]