"""S0 milestone: the energy equation cannot violate its own bounds."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from storage import Storage, load_config, storage_from_config  # noqa: E402


def test_bounds_and_balance_under_random_input():
    rng = np.random.default_rng(0)
    s = Storage(e_max_mj=440.6, leak_mj_per_day=432.0,
                sleep_mj_per_day=285.0, timestep_s=60)
    e_init = s.e
    for _ in range(20000):
        s.add_harvest(float(rng.exponential(2.0)))
        if rng.random() < 0.3:
            s.spend(float(rng.uniform(0, 30)))
        s.apply_overhead()
        assert 0.0 <= s.e <= s.e_max_mj, "energy left its bounds"
    assert s.balance_error(e_init) < 1e-6, "energy not conserved"


def test_spend_respects_reserve():
    s = Storage(440.6, 0.0, 0.0, 60, e=100.0)
    assert not s.spend(50.0, reserve=60.0)   # 100-50=50 < 60
    assert s.e == 100.0
    assert s.spend(30.0, reserve=60.0)       # 100-30=70 >= 60
    assert s.e == 70.0


def test_overflow_is_wasted_not_stored():
    s = Storage(100.0, 0.0, 0.0, 60, e=90.0)
    s.add_harvest(50.0)
    assert s.e == 100.0
    assert abs(s.wasted - 40.0) < 1e-9


def test_never_goes_negative():
    s = Storage(100.0, 86400.0 * 10, 0.0, 60, e=1.0)
    for _ in range(100):
        s.apply_overhead()
    assert s.e == 0.0
    assert s.outage_steps > 0


def test_config_loads():
    cfg = load_config()
    s = storage_from_config(cfg, timestep_s=60)
    assert s.e_max_mj > 0 and s.overhead_per_step > 0