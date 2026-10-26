"""
Physics-based footstep energy: gait subject mass -> peak force -> mechanical
energy under a compliant tile -> electrical energy after piezo coupling.

    F_peak  = k * m * g                      k ~ 1.0-1.2 at self-selected speed
    E_mech  = 1/2 * F_peak * delta           delta = tile deflection (1-3 mm)
    E_elec  = E_mech * eta                   eta = piezo coupling (1-5 %)

Only the body-mass term comes from data (Horst et al. 2017, Gait & Posture
58:476-480, CC BY 4.0, Mendeley yrpbd8fhc4). CASAS visits are NOT joined to
gait subjects: CASAS does not record who is walking, so the population is
sampled, not matched.

Two assumptions are not in the data and are labelled as such in
config/energy.yaml: delta and eta. k is a fixed physics constant here.

Outputs
  config/gait_calibration.csv   per-subject table (small, committed)
  stdout                        validation against published tile measurements

Usage
    python src/gait_energy.py                  # calibration + validation
    python src/gait_energy.py --delta 2 --eta 0.03
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "gait" / "gait_grf_subject.txt"       # 3 KB, see .gitignore note
CALIB = ROOT / "config" / "gait_calibration.csv"

G = 9.81          # m/s^2
# F_peak / body weight, per foot, at self-selected speed. MEASURED from the raw
# trials (1278 recordings, 128 subjects): median 1.20, IQR 1.17-1.25. Slope of
# peak force vs mass is 10.9 N/kg against k*g = 10.8 predicted, so F ~ m holds.
K_PEAK = 1.20

# Published floor-tile measurements (KMITL EHFT study, as cited in the brief).
PUBLISHED = {"<50 kg": 11.0, ">70 kg": 32.0}   # mJ per step


def load_subjects(path: Path = META) -> pd.DataFrame:
    """Subject metadata. Columns: sid, female, age, mass_kg, height_m, shoe_eu."""
    raw = pd.read_csv(path, sep=";")
    raw.columns = ["sid", "female", "age", "mass_kg", "height_m", "shoe_eu"]
    return raw.dropna(subset=["mass_kg"]).reset_index(drop=True)


def peak_force_n(mass_kg, k: float = K_PEAK):
    return k * np.asarray(mass_kg, dtype=float) * G


def mech_energy_mj(force_n, delta_mm: float):
    """Energy stored in a compliant surface under a peak force: 1/2 F x delta."""
    return 0.5 * np.asarray(force_n, dtype=float) * (delta_mm * 1e-3) * 1e3


def elec_energy_mj(e_mech_mj, eta: float):
    return np.asarray(e_mech_mj, dtype=float) * eta


def footstep_mj(mass_kg, delta_mm: float, eta: float, k: float = K_PEAK):
    """Mass -> electrical mJ per step, one value per mass."""
    return elec_energy_mj(mech_energy_mj(peak_force_n(mass_kg, k), delta_mm), eta)


def sample_population(subjects: pd.DataFrame, n: int, rng: np.random.Generator):
    """
    Bootstrap body masses from the 128 recorded subjects. A sample of walkers,
    not a single scalar: the caller reports percentiles, never one number.
    """
    return rng.choice(subjects.mass_kg.to_numpy(), size=n, replace=True)


def validate(subjects: pd.DataFrame, delta_mm: float, eta: float) -> dict:
    """
    Compare model output with the published floor-tile groups.

    Two checks, reported separately:
      range  - does the model land in the published absolute range?
      ratio  - does the model reproduce the >70 kg / <50 kg ratio?
    The range check can be passed by tuning eta. The ratio check cannot be
    tuned: under F ~ m it is fixed by the mass ratio alone.
    """
    lo = subjects[subjects.mass_kg < 50].mass_kg
    hi = subjects[subjects.mass_kg > 70].mass_kg
    e_lo = footstep_mj(lo, delta_mm, eta)
    e_hi = footstep_mj(hi, delta_mm, eta)
    model_ratio = float(hi.mean() / lo.mean())
    return {
        "n_lt50": int(len(lo)), "n_gt70": int(len(hi)),
        "model_lt50_mean": float(e_lo.mean()), "model_gt70_mean": float(e_hi.mean()),
        "published_lt50": PUBLISHED["<50 kg"], "published_gt70": PUBLISHED[">70 kg"],
        "model_ratio_gt70_over_lt50": model_ratio,
        "published_ratio": PUBLISHED[">70 kg"] / PUBLISHED["<50 kg"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", type=float, default=2.0, help="tile deflection, mm")
    ap.add_argument("--eta", type=float, default=0.03, help="piezo coupling, fraction")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    subj = load_subjects()
    print(f"{len(subj)} subjects, mass {subj.mass_kg.min():.1f}-{subj.mass_kg.max():.1f} kg "
          f"(mean {subj.mass_kg.mean():.1f})")

    subj = subj.assign(
        peak_force_n=peak_force_n(subj.mass_kg).round(1),
        e_mech_mj=mech_energy_mj(peak_force_n(subj.mass_kg), a.delta).round(1),
        e_elec_mj=footstep_mj(subj.mass_kg, a.delta, a.eta).round(2),
    )
    CALIB.parent.mkdir(parents=True, exist_ok=True)
    subj.to_csv(CALIB, index=False)
    print(f"wrote {CALIB.relative_to(ROOT)}  ({len(subj)} rows)")

    rng = np.random.default_rng(a.seed)
    pop = footstep_mj(sample_population(subj, 10_000, rng), a.delta, a.eta)
    print(f"\npopulation footstep energy, delta={a.delta} mm, eta={a.eta:.3f}:")
    for q in (5, 50, 95):
        print(f"  p{q:02d}: {np.percentile(pop, q):6.2f} mJ")

    v = validate(subj, a.delta, a.eta)
    print("\nvalidation against published floor-tile groups")
    print(f"  <50 kg  model {v['model_lt50_mean']:6.2f} mJ  "
          f"published {v['published_lt50']:.0f} mJ  (n={v['n_lt50']})")
    print(f"  >70 kg  model {v['model_gt70_mean']:6.2f} mJ  "
          f"published {v['published_gt70']:.0f} mJ  (n={v['n_gt70']})")
    print(f"  ratio   model {v['model_ratio_gt70_over_lt50']:.2f}  "
          f"published {v['published_ratio']:.2f}")


if __name__ == "__main__":
    main()
