"""
One node's energy store, plus the config loader.

    E[t+1] = clip( E[t] + harvest - spent - leak,  0,  E_max )

Everything above E_max is lost and accumulated as `wasted`. Everything the node
cannot afford is a failed spend, not a negative balance.

All energy in millijoules. Constants come from config/energy.yaml — never
hardcode an energy number in Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "config" / "energy.yaml"


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CFG_PATH).read_text())


def val(node: dict, key: str = "value"):
    """Pull a value out of a tagged config entry, honouring SWEPT defaults."""
    if not isinstance(node, dict):
        return node
    if "default" in node:
        return node["default"]
    return node.get(key)


@dataclass
class Storage:
    e_max_mj: float
    leak_mj_per_day: float
    sleep_mj_per_day: float
    timestep_s: float
    e: float = field(default=None)

    # accounting
    harvested: float = 0.0
    spent: float = 0.0
    leaked: float = 0.0
    wasted: float = 0.0
    outage_steps: int = 0

    def __post_init__(self):
        if self.e is None:
            self.e = 0.5 * self.e_max_mj
        self.overhead_per_step = (
            (self.leak_mj_per_day + self.sleep_mj_per_day) * self.timestep_s / 86400.0
        )

    # --- queries -----------------------------------------------------------
    def can_afford(self, cost: float, reserve: float = 0.0) -> bool:
        return self.e - cost >= reserve

    @property
    def soc(self) -> float:
        return self.e / self.e_max_mj if self.e_max_mj else 0.0

    # --- mutations ---------------------------------------------------------
    def spend(self, cost: float, reserve: float = 0.0) -> bool:
        """Spend if affordable above `reserve`. Returns whether it happened."""
        if not self.can_afford(cost, reserve):
            return False
        self.e -= cost
        self.spent += cost
        return True

    def add_harvest(self, mj: float) -> None:
        self.harvested += mj
        self.e += mj
        if self.e > self.e_max_mj:
            self.wasted += self.e - self.e_max_mj
            self.e = self.e_max_mj

    def apply_overhead(self) -> None:
        drain = min(self.overhead_per_step, self.e)
        self.e -= drain
        self.leaked += drain
        if self.e <= 0.0:
            self.e = 0.0
            self.outage_steps += 1

    # --- invariant ---------------------------------------------------------
    def balance_error(self, e_init: float) -> float:
        """Should be ~0: everything in equals everything out plus what's left."""
        return abs((e_init + self.harvested)
                   - (self.spent + self.leaked + self.wasted + self.e))


def storage_from_config(cfg: dict, timestep_s: float,
                        sleep_ua: float | None = None) -> Storage:
    st = cfg["storage"]
    opt = st["options"][st["selected"]]
    v_max = opt["v_max"]
    leak_mj_day = opt["leak_ua"] * 1e-6 * v_max * 86400 * 1000
    if sleep_ua is None:
        sleep_ua = val(cfg["overhead"]["mcu_sleep_ua"])
    rail = val(cfg["tasks"]["rail_voltage_v"])
    sleep_mj_day = sleep_ua * 1e-6 * rail * 86400 * 1000
    return Storage(e_max_mj=opt["e_max_mj"], leak_mj_per_day=leak_mj_day,
                   sleep_mj_per_day=sleep_mj_day, timestep_s=timestep_s)