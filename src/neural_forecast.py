"""
GRU forecaster for per-node event counts in 15-minute windows (Phase 4).

Predicts the next window's n_events for every real node jointly, from the
previous LOOKBACK windows (24 h at 15 min). This is a FORECASTING-ACCURACY
result. docs/findings.md F1 shows that better forecasts do not improve
scheduling on this data, so do not report this model as a scheduling gain.

Training pools the training splits of all given homes into one model. Each
home is then scored on its own test split, against the same naive baselines
as baseline.py, using that home's own training data:
  persist_last  previous window's count
  hour_mean     training-split mean for this node and hour of day

Inputs per window (shared by all homes and nodes):
  log1p(n_events) x 4 nodes, covered x 4 nodes, sin/cos hour, sin/cos weekday
Target: log1p(n_events) per node. Loss is masked to covered target windows.
Metrics are computed in count space, like baseline.py.

Usage
    python src/neural_forecast.py hh104                          # one home
    python src/neural_forecast.py hh104 hh101 hh107 mn50 mn51    # pooled
    python src/neural_forecast.py hh104 --seeds 5
Output
    reports/neural_<tag>/metrics.json
    reports/committed/neural_<tag>.csv   (small summary)
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import f1_score

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"

NODES = ["floor", "furniture", "ambient", "door"]   # real nodes only; gym is synthetic
LOOKBACK = 96          # 24 h of 15-min windows
HIDDEN = 32
BATCH = 128
LR = 1e-3
MAX_EPOCHS = 300
PATIENCE = 15          # early stop on validation loss
KEYS = ["mae", "rmse", "occ_acc", "occ_f1"]


def load_grid(trace: str):
    w = pd.read_parquet(PROC / f"windows_{trace}.parquet")
    w = w[w.node_id.isin(NODES)]
    idx = pd.DatetimeIndex(sorted(w.window_start.unique()))

    def wide(col):
        return (w.pivot(index="window_start", columns="node_id", values=col)
                 .reindex(index=idx, columns=NODES))

    counts = wide("n_events").fillna(0).to_numpy(float)
    covered = wide("covered").fillna(False).astype(bool).to_numpy()
    split = w.groupby("window_start").split.first().reindex(idx).to_numpy()
    return idx, counts, covered, split


def build_inputs(idx, counts, covered):
    hour = idx.hour + idx.minute / 60.0
    dow = idx.dayofweek
    cal = np.stack([np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24),
                    np.sin(2 * np.pi * dow / 7), np.cos(2 * np.pi * dow / 7)], axis=1)
    feats = np.concatenate([np.log1p(counts), covered.astype(float), cal], axis=1)
    return feats.astype(np.float32)


def make_samples(idx, feats, counts, covered, split):
    """
    Sample i uses windows [i-LOOKBACK, i) as input and window i as target.
    Each sample belongs to the split of its target window.
    """
    T = len(feats)
    idxs = np.arange(LOOKBACK, T)
    X = np.stack([feats[i - LOOKBACK:i] for i in idxs])            # (N, L, F)
    Y = np.log1p(counts[idxs]).astype(np.float32)                  # (N, nodes)
    M = covered[idxs].astype(np.float32)                           # loss mask
    return {
        "X": torch.tensor(X), "Y": torch.tensor(Y), "M": torch.tensor(M),
        "raw": counts[idxs], "prev_cov": covered[idxs - 1],
        "prev_count": counts[idxs - 1], "split": split[idxs],
        "hour": idx[idxs].hour.to_numpy(),
    }


class GRUForecaster(nn.Module):
    def __init__(self, n_in: int, hidden: int, n_out: int):
        super().__init__()
        self.gru = nn.GRU(n_in, hidden, batch_first=True)
        self.head = nn.Linear(hidden, n_out)

    def forward(self, x):
        out, _ = self.gru(x)
        return self.head(out[:, -1])


def masked_mse(pred, y, m):
    return ((pred - y) ** 2 * m).sum() / m.sum().clamp(min=1.0)


def split_of(data, name):
    sel = np.where(data["split"] == name)[0]
    return {k: (v[sel] if isinstance(v, np.ndarray) else v[torch.as_tensor(sel)])
            for k, v in data.items()}


def pool(parts):
    out = {}
    for k in parts[0]:
        vals = [p[k] for p in parts]
        out[k] = torch.cat(vals) if isinstance(vals[0], torch.Tensor) else np.concatenate(vals)
    return out


def train(seed: int, tr: dict, va: dict, n_in: int):
    torch.manual_seed(seed)
    np.random.seed(seed)
    model = GRUForecaster(n_in, HIDDEN, len(NODES))
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    best, best_state, bad = np.inf, None, 0
    n = len(tr["X"])
    for epoch in range(MAX_EPOCHS):
        model.train()
        perm = torch.randperm(n)
        for b in range(0, n, BATCH):
            ii = perm[b:b + BATCH]
            loss = masked_mse(model(tr["X"][ii]), tr["Y"][ii], tr["M"][ii])
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            v = masked_mse(model(va["X"]), va["Y"], va["M"]).item()
        if v < best - 1e-5:
            best, best_state, bad = v, copy.deepcopy(model.state_dict()), 0
        else:
            bad += 1
            if bad >= PATIENCE:
                break
    model.load_state_dict(best_state)
    return model, best, epoch + 1


def hour_mean_pred(tr: dict, te: dict):
    """Per-node mean count by hour of day, from one home's training split."""
    out = np.zeros(te["raw"].shape, dtype=float)
    cov_tr = tr["M"].numpy().astype(bool)
    for j in range(len(NODES)):
        means = (pd.Series(tr["raw"][:, j][cov_tr[:, j]])
                 .groupby(tr["hour"][cov_tr[:, j]]).mean()
                 .reindex(range(24)).to_numpy())
        out[:, j] = means[te["hour"]]
    return out


def score(pred_counts, true_counts, mask):
    """Pooled over masked (sample, node) entries, as in baseline.py."""
    p, t = pred_counts[mask], true_counts[mask]
    err = p - t
    yt, yp = (t > 0).astype(int), (p > 0.5).astype(int)
    return {
        "n": int(mask.sum()),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "occ_acc": float((yt == yp).mean()),
        "occ_f1": float(f1_score(yt, yp, zero_division=0)),
    }


def summarise(runs):
    out = {k: {"mean": float(np.mean([r[k] for r in runs])),
               "sd": float(np.std([r[k] for r in runs], ddof=1)) if len(runs) > 1 else 0.0}
           for k in KEYS}
    out["n"] = runs[0]["n"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="*", default=["hh104"])
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    traces = a.traces or ["hh104"]
    tag = traces[0] if len(traces) == 1 else "+".join(traces)

    per = {}
    for t in traces:
        idx, counts, covered, split = load_grid(t)
        feats = build_inputs(idx, counts, covered)
        data = make_samples(idx, feats, counts, covered, split)
        per[t] = {s: split_of(data, s) for s in ("train", "val", "test")}
    n_in = feats.shape[1]
    tr = pool([per[t]["train"] for t in traces])
    va = pool([per[t]["val"] for t in traces])
    print(f"training on {len(traces)} home(s): {len(tr['X'])} train samples, "
          f"{len(va['X'])} validation samples")

    # Evaluation mask: test target covered AND previous window covered, so
    # persist_last is defined on exactly the same windows as the model.
    mask, raw, persist, hourm = {}, {}, {}, {}
    for t in traces:
        te = per[t]["test"]
        mask[t] = te["M"].numpy().astype(bool) & te["prev_cov"]
        raw[t] = te["raw"]
        persist[t] = te["prev_count"]
        hourm[t] = hour_mean_pred(per[t]["train"], te)

    gru_pred = {t: [] for t in traces}
    val_losses = []
    for seed in range(a.seeds):
        model, val_loss, epochs = train(seed, tr, va, n_in)
        val_losses.append(val_loss)
        model.eval()
        with torch.no_grad():
            for t in traces:
                pred_log = model(per[t]["test"]["X"]).numpy()
                gru_pred[t].append(np.clip(np.expm1(pred_log), 0, None))
        print(f"seed {seed}: validation MSE {val_loss:.3f} ({epochs} epochs)")

    rows, detail = [], {}
    for name in traces + ["pooled"]:
        ts = traces if name == "pooled" else [name]
        flat_raw = np.concatenate([raw[x] for x in ts])
        flat_mask = np.concatenate([mask[x] for x in ts])
        methods = {
            "persist_last": score(np.concatenate([persist[x] for x in ts]), flat_raw, flat_mask),
            "hour_mean": score(np.concatenate([hourm[x] for x in ts]), flat_raw, flat_mask),
        }
        gru_runs = [score(np.concatenate([gru_pred[x][s] for x in ts]), flat_raw, flat_mask)
                    for s in range(a.seeds)]
        methods["gru"] = summarise(gru_runs)
        detail[name] = methods
        for method, r in methods.items():
            if method == "gru":
                vals = {k: round(r[k]["mean"], 3) for k in KEYS}
                sds = {f"{k}_sd": round(r[k]["sd"], 3) for k in KEYS}
            else:
                vals = {k: round(r[k], 3) for k in KEYS}
                sds = {f"{k}_sd": 0.0 for k in KEYS}
            rows.append({"home": name, "method": method, **vals, **sds, "n": r["n"]})

    outdir = REPORTS / f"neural_{tag}"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics.json").write_text(json.dumps(
        {"homes_trained_on": traces, "lookback": LOOKBACK, "hidden": HIDDEN,
         "nodes": NODES, "seeds": a.seeds, "val_mse": val_losses,
         "detail": detail}, indent=2))

    summary = pd.DataFrame(rows)
    committed = REPORTS / "committed"
    committed.mkdir(parents=True, exist_ok=True)
    summary.to_csv(committed / f"neural_{tag}.csv", index=False)
    print()
    print(summary.to_string(index=False))
    print(f"\nwrote {outdir}/metrics.json")


if __name__ == "__main__":
    main()
