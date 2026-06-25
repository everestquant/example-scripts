---
name: eiq-model-implementation
description: |
  Write and validate the modeling code behind a new Everesteer Himalayas (futures) model — either a templated built-in via MCP quick_train, or your own training script run on Everesteer compute via MCP custom_train. Use when an idea needs real modeling code (a new model type, a custom fit/predict routine, an ensemble) rather than just a different hyperparameter sweep. Covers the fit/predict contract, leakage-safe validation, and the futures-specific patterns that move EAC/AIMC.
---

# Implementing a Model for the Everesteer Himalayas Tournament

The Himalayas tournament asks you to rank global futures **chains** (grouped into **clusters**) at each **exped** against the `target_everest_20` target. This skill is about the *code* that produces those rankings: how to express a model so it runs cleanly on Everesteer compute, and how to convince yourself the model is real before you put value behind it.

You never touch any platform-internal repository. Everything here is built on the public `everestapi` SDK, the Everesteer MCP tools, and the helper code in `example-scripts/` plus a `models/` directory you own.

## Two ways to produce a model

1. **`quick_train` (templated).** Pick a built-in model family (LightGBM, XGBoost, ridge, MLP, …), pass a config, and the platform fits it for you. No code to write — go straight to `eiq-experiment-design`. Use this for baselines and for anything a standard learner handles well.

2. **`custom_train` (your code).** When the modeling idea is not expressible as a templated config — a bespoke ensemble, a residualized target, a custom fit loop, an exotic learner — you write a training script, ship it to Everesteer GPU/CPU compute with `custom_train`, and pull the fitted artifact back with `get_model_download_url`.

This skill focuses on case 2. Reach for it only when `quick_train` genuinely can't express what you want — extra moving parts mean extra ways to leak or break.

## The model contract

A custom model is any object with two methods. Keep the surface this small; the harness does the rest.

```python
import pandas as pd
import numpy as np


class MyEverestModel:
    """A Himalayas model. Implements fit() and predict() only."""

    def __init__(self, **hyperparams):
        # Stash config; build nothing heavy here.
        self.hp = hyperparams
        self._fitted = None

    def fit(self, X: pd.DataFrame, y: pd.Series, sample_weight=None) -> "MyEverestModel":
        """Train.

        X : feature frame. Columns are obfuscated, quintile-binned names of the
            form feature_<theme>_<n>, each value an integer in {0,1,2,3,4}.
            Rows are (exped, chain) observations.
        y : target_everest_20, rank-normalized forward return, aligned to X.index.
        sample_weight : optional per-row weights (see cluster weighting below).
        """
        Xn = X.to_numpy(dtype=np.float32)
        yn = y.to_numpy(dtype=np.float32)
        # ... fit your learner on Xn/yn (+ weights) ...
        self._fitted = ...  # the trained object
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        """Score. One value per row, index identical to X.index."""
        if self._fitted is None:
            raise RuntimeError("predict() called before fit().")
        raw = self._fitted.predict(X.to_numpy(dtype=np.float32))
        return pd.Series(raw, index=X.index)
```

Rules that matter:

- **DataFrame in, Series out.** Convert to numpy *inside* the method, never at the boundary — the index is your contract with the scorer and you must preserve it.
- **One score per row, index-aligned.** The platform ranks your raw scores cross-sectionally within each exped, so the absolute scale is irrelevant — only the within-exped ordering of chains is scored. Don't pre-normalize to `[-1, 1]`; don't drop or reorder rows.
- **No NaNs out.** A single NaN poisons that exped's rank. Fill or guard before returning.
- **Lazy-import optional deps** with an actionable message, so a missing package fails loudly rather than at fit time:

```python
def fit(self, X, y, sample_weight=None):
    try:
        from catboost import CatBoostRegressor
    except ImportError as e:
        raise ImportError(
            "catboost not installed — add it to custom_train(requirements=[...])."
        ) from e
    ...
```

Keep custom models in a participant-owned `models/` directory (e.g. `models/everest_catboost.py`) so model code stays separate from experiment glue.

## Running it on Everesteer compute

The flow with the SDK and MCP tools:

```python
from everestapi import EverestAPI
client = EverestAPI(api_key="...", tournament="futures")

# 1. Get the data locally to develop/smoke-test against.
client.download_dataset(universe="futures", split="train", output_path="train.parquet")
client.download_dataset(universe="futures", split="validation", output_path="val.parquet")

# 2. Ship a training script to compute (GPU for heavy learners).
#    gpu in {T4, A40, A100}; ~$0.50–1.79/hr; the pod has the EverestAPI SDK + PyTorch +
#    common ML libs preinstalled. Add extra deps with requirements=[...].
job = client.custom_train(script_path="train.py", gpu="A100", max_hours=2.0)

# 3. Wait, then retrieve the fitted artifact.
result = client.wait_for_job(job["job_id"])
client.download_model(job["job_id"], output_path="model.pkl")
```

Via MCP the equivalents are `custom_train`, `get_job_status`, and `get_model_download_url`; check budget first with `get_compute_credits`, fetch column names with `get_dataset_schema`, and use `download_dataset` for the parquet. `custom_train` runs on metered GPU/CPU — `quick_train` is cheaper for anything templated. (The participant-facing SDK takes `script_path="train.py"`; the raw MCP `custom_train` tool takes `script=<python source string>` instead — same job, different entrypoint.)

The script runs on the compute pod with the EverestAPI SDK (and PyTorch + common ML libs) preinstalled. Keep it self-contained — download/load data, fit, and dump the artifact — so it has no hidden dependency on your local environment.

<!-- VERIFY: the exact output filename/path the artifact must be written to so
get_model_download_url / download_model can retrieve it (model.pkl in the working dir is
the conservative assumption). Confirm against the installed everestapi version or the
custom_train MCP tool schema. -->

A defensive `train.py` skeleton:

```python
import pandas as pd, pickle
from models.everest_catboost import MyEverestModel

def main():
    train = pd.read_parquet("train.parquet")
    feats = [c for c in train.columns if c.startswith("feature_")]
    model = MyEverestModel(iterations=600, depth=6)
    model.fit(train[feats], train["target_everest_20"])
    with open("model.pkl", "wb") as f:
        pickle.dump(model, f)

if __name__ == "__main__":
    main()
```

> **Pickle is RCE-equivalent.** Only `pickle.load` artifacts from jobs *you* launched. Never load a model file handed to you by someone else without inspecting it in isolation.

## Validation gate — do this before you stake

The hardest part of the tournament is not training; it's knowing whether the number you see is real. You will eventually decide whether to **stake real value** on a model, and that decision is only as good as your out-of-sample estimate. Treat validation as a gate, not a formality.

**Step 1 — smoke run on a small exped subset.** Fit on a slice, predict, and assert the contract holds:

```python
sub = train[train["exped"].isin(train["exped"].unique()[:200])]
m = MyEverestModel().fit(sub[feats], sub["target_everest_20"])
p = m.predict(sub[feats])
assert isinstance(p, pd.Series) and len(p) == len(sub)
assert p.index.equals(sub.index) and not p.isna().any()
```

**Step 2 — sanity-bound the CORR.** Compute per-exped rank correlation against the target on a *held-out* split (validation, never the rows you trained on):

```python
metrics = EverestAPI.evaluate(preds, val, target="target_everest_20")
```

A healthy futures model lands at a **small positive** CORR — on the order of a few hundredths. Both tails are red flags:

- **CORR near zero or negative:** the model isn't learning, or features/target are misaligned. Check the index join and the feature filter.
- **CORR suspiciously high** (e.g. an order of magnitude above what `ai_model` and the leaderboard achieve): assume **leakage** until proven otherwise. The usual culprits are evaluating on training rows, leaking the target through a derived column, or an index that lets future expeds bleed in.

Run `run_validation_diagnostics` (MCP) for the platform's own read on feature exposure and per-cluster behavior before trusting a number.

**Step 3 — guard the fit/predict loop against subtle leakage.** Early stopping is the classic trap: if a validation fold steers the stopping point and that same fold feeds your reported metric, you've contaminated the estimate. Tune stopping inside a nested split, then report on data that played no role in fitting. Any per-feature standardization, target encoding, or neutralization must be fit on train only and *applied* to validation — never re-fit there.

## Patterns that move EAC/AIMC (and why)

Payout is `0.75·CORR + 2.25·AIMC`, capped at ±25%. The 2.25 weight on **AIMC** (AI Model Contribution — the contribution beyond the live stake-weighted ai-model consensus) means a merely-accurate model that echoes consensus pays little. AIMC is the live payout target, but it's only measurable once rounds resolve, so offline you optimize **EAC** (Everest Alpha Contribution — your unique signal beyond the static benchmark model `eiq_minera_model`, same contribution formula as AIMC but vs the static benchmark). **Optimize EAC offline — it's the validation-time proxy for AIMC, the metric that actually pays once a round resolves.** Uniqueness is the lever; the patterns below all chase EAC/AIMC.

- **Residualize the target against `ai_model`.** Train on the residual of `target_everest_20` after projecting out the benchmark, so the model can only learn what the benchmark misses. Raises EAC/AIMC by lowering correlation with the static benchmark and the meta-model.
- **Neutralize predictions against the benchmark / heavy features.** OLS-project your raw scores onto the benchmark (or a few dominant feature exposures) and subtract the projection. Lowers correlation to consensus, raising EAC/AIMC, usually at a modest CORR cost — tune the neutralization proportion.
- **Blend multiple targets.** The auxiliary peak targets (k2, lhotse, makalu, … at 20d/60d) carry related-but-distinct signal; a weighted blend can be steadier than chasing `everest_20` alone. (Watch the known inverse pair — `manaslu` and `nanga_parbat` are anti-correlated; never include both raw.)
- **Bag / ensemble.** Average several seeds or row-subsamples to cut variance. Steadier rankings translate to steadier AIMC across rounds, which matters more than a single hot exped.

Each of these earns its keep only if it raises differentiated signal — accuracy that everyone already has is nearly free on the payout formula.

## Futures-specific concerns

- **Cluster-aware sample weighting.** Clusters differ wildly in size and volatility (eq, rates, fossil_energy, agriculture, fx, eu_power, metals, volatility). Equal per-row weighting lets the largest cluster dominate the fit. Pass `sample_weight` to balance influence — e.g. inverse-frequency by cluster — so the model generalizes across the universe rather than overfitting one corner.
- **Missing chains / contracts.** The live universe shifts as chains onboard, expire, or fall out of coverage; a chain present in training may be absent live (and vice versa). Never assume a fixed instrument set. Reindex defensively and impute missing features within {0..4} rather than dropping rows.
- **Robustness across clusters.** A model with a great blended CORR but negative CORR in two clusters is fragile. Check the per-cluster breakdown (`get_model_per_exped_breakdown`, `run_validation_diagnostics`) and prefer broadly-positive models over ones that win on one cluster.

## Where this hands off

Once the model code passes the validation gate:

1. Hand off to **`eiq-experiment-design`** to run multiple rounds, compare configs, and scale the winners until they plateau.
2. Then **`eiq-futures-submission`** to deploy the chosen model and submit predictions.

Don't skip ahead to submission from a single promising backtest — the experiment loop is what separates a real edge from a lucky split.
