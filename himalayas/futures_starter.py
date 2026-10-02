#!/usr/bin/env python3
"""
Everesteer Futures Tournament, Starter Example

The Himalayas (futures) tournament runs on the Atlas dataset, with one round
each weekday, Monday to Friday. The splits:

  1. fit on `train`:      LABELED (features + target_* columns), the largest split
  2. `validation`:        the PRACTICE board, target columns BLANKED and scored
                            server-side. Answers stay server-side, always
  3. `live`:              the current round, target columns BLANKED. One round:
                            predict every row

This script runs the whole loop once: orient, download, fit a LightGBM baseline,
score it on an embargoed holdout, predict the split that is scored right now,
and submit it down the correct lane: to today's round if its window is open,
to the practice board if not.

It produces:
  - baseline_predictions.parquet (+ .csv): id + prediction for the scored split
  - baseline_model.pkl:          the model as a cloudpickled predict(). Upload it
                                  and enable auto-submit (section 8)

Usage:
    pip install "everestapi>=0.3.38" lightgbm scikit-learn pandas pyarrow cloudpickle
    export EIQ_API_KEY="..."               # from onboarding
    export EIQ_BASE_URL="https://api.everesteer.ai"
    python himalayas/futures_starter.py

Connecting: production needs only your API key. A staging or preview host also
sits behind Cloudflare Access and will bounce an API-key-only request at the edge
(a 302 to a login page, or error 1010), which does not look like an auth failure.
The SDK handles it for you: set CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET in
the environment (or pass cf_access_client_id= / cf_access_client_secret= to
EverestAPI) and the service-token headers ride alongside your key. Interactive
`cloudflared access login <host>` also works.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cloudpickle
import lightgbm as lgb
import pandas as pd
from everestapi import EverestAPI

client = EverestAPI(
    api_key=os.environ["EIQ_API_KEY"],
    base_url=os.environ.get("EIQ_BASE_URL", "https://api.everesteer.ai"),
    tournament="futures",
)
OUTPUT_DIR = Path(__file__).resolve().parent


def bail(step: str, exc: Exception) -> None:
    """Fail with something you can act on instead of a traceback."""
    print(f"\n{step} failed: {exc}")
    print("If this is a staging or preview host, check the Cloudflare Access note")
    print("in this file's docstring. An edge bounce is not an auth failure.")
    sys.exit(1)


def read_round() -> dict:
    """The current round, or {} when there is none.

    get_current_round 404s when no round is active at all (get_schedule() has
    the clocks then). Between rounds it answers with submission_window
    "closed": the platform still accepts an upload then, but DOES NOT SCORE it,
    so this script only enters a round while the window is "open".
    """
    try:
        return client.get_current_round(tournament="futures") or {}
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status_code", None) == 404:
            return {}
        raise


# =====================================================================
# 1. Orient: is today's round taking scored predictions?
# =====================================================================
try:
    current = read_round()
except Exception as exc:  # noqa: BLE001, first network call, report it plainly
    bail("get_current_round", exc)

round_open = current.get("submission_window") == "open"
print("Round orientation")
print(f"  round (exped)      : {current.get('exped')}")
print(f"  status             : {current.get('status')}")
print(f"  window             : {current.get('submission_window')}   (only 'open' is scored)")
print(f"  closes at          : {current.get('close_at')}")
print(f"  rows to cover      : {current.get('universe_size')}")

# =====================================================================
# 2. Schema: read the target and features, never hardcode them
# =====================================================================
try:
    schema = client.get_dataset_schema()
except Exception as exc:  # noqa: BLE001
    bail("get_dataset_schema", exc)

targets = schema.get("targets") or []
# The graded column is `primary_target` and only `primary_target`. It is NOT
# targets[0]: `targets` is the dataset's own target block in its own order, so
# the first entry is just whichever name happens to sort first, which is a
# different column on most datasets. Fitting that one instead fails silently.
target_col = schema.get("primary_target")
if not target_col:
    bail(
        "get_dataset_schema",
        RuntimeError("schema declares no primary_target - refusing to guess"),
    )
print(f"\nTarget: {target_col}   (schema advertises {len(targets)} target(s))")

# The missing sentinel is a dataset fact too. `feature_encoding` declares the
# bin count, the value range and the sentinel; read it rather than assuming -1.
# It is absent on a tree that does not declare its encoding, which is not
# permission to assume a default either, so fall back only as a last resort.
encoding = schema.get("feature_encoding") or {}
MISSING = encoding.get("missing")
if MISSING is None:            # absent OR published as null; neither declares a sentinel
    MISSING = -1.0
print(f"Missing sentinel: {MISSING!r}   (from schema['feature_encoding'])")
if schema.get("primary_target_listed") is False:
    # Expected on a dataset that publishes the graded column under an alias:
    # it is served and scored either way. Predict it, do not substitute.
    print("  note: graded column not listed among 'targets' - predict it anyway")

# =====================================================================
# 3. Download: train, plus whichever split is scored right now
# =====================================================================
# The two upload lanes score DIFFERENT splits, and their ids do not overlap:
#   live       -- today's round, scored by submit_futures_predictions
#   validation -- the practice board, scored by submit_validation_diagnostics
# `scored_exped` records WHICH round these rows came from. Step 9 needs it: the
# lane is decided by the rows in hand, and "which rows are these" is a fact
# about the download, not about whatever the clock says some minutes later.
scored_split = "live" if round_open else "validation"
scored_exped = current.get("exped") if round_open else None
try:
    train = pd.read_parquet(client.download_dataset(split="train"))
    scored = pd.read_parquet(client.download_dataset(split=scored_split))
except Exception as exc:  # noqa: BLE001
    bail(f"download_dataset(split='train' / '{scored_split}')", exc)

# Exped column (each trading day = one expedition)
EXPED_COL = "exped"
# Select features from the PUBLISHED list, never by a column-name prefix. The
# verbose schema carries set membership (the plain call gives only counts); the
# dataset publishes one set, `all`.
try:
    feature_sets = client.get_dataset_schema(verbose=True).get("feature_sets") or {}
except Exception as exc:  # noqa: BLE001
    bail("get_dataset_schema(verbose=True)", exc)
feat_cols = [c for c in feature_sets.get("all", []) if c in train.columns]
if not feat_cols:
    bail("feature list", RuntimeError("schema published no `all` feature set"))

print(f"\nTrain : {len(train):>8,} rows  |  {train[EXPED_COL].nunique()} expeds")
print(f"Scored: {len(scored):>8,} rows  |  split={scored_split}")
print(f"Features: {len(feat_cols)}")
if round_open and scored_exped not in set(scored[EXPED_COL]):
    bail("download_dataset(split='live')",
         RuntimeError(f"live does not carry the open round {scored_exped!r}"))

# =====================================================================
# 4. Split train into fit + embargoed holdout
# =====================================================================
# Neither split we predict is a place to measure yourself: both ship with their
# targets blanked. So we evaluate on a holdout carved from the END of train,
# with an embargo between fit and holdout: the target realises over 20 trading
# days, so the last fitting expeds already encode the first holdout expeds.
# train's exped tokens are zero-padded, so their string order is their time order.
HOLDOUT_EXPEDS = 100
EMBARGO = 20
expeds = sorted(train[EXPED_COL].unique())
holdout_expeds = set(expeds[-HOLDOUT_EXPEDS:])
fit_expeds = set(expeds[: -HOLDOUT_EXPEDS - EMBARGO])

fit_df = train[train[EXPED_COL].isin(fit_expeds)]
holdout_df = train[train[EXPED_COL].isin(holdout_expeds)]
print(
    f"\nFit: {len(fit_df):,} rows | Holdout: {len(holdout_df):,} rows | Embargo: {EMBARGO} expeds"
)


# Feature values are ENCODED into bins. The bin count differs between datasets,
# so read `schema["feature_encoding"]` rather than assuming one: it declares
# the bin count, the value range and the `missing` sentinel (commonly -1.0, set
# where the source was not onboarded for that instrument/date). Treat missing as
# NaN or as its own category, NEVER as an ordinal below the lowest real bin.
def features_matrix(df, cols):
    """Bin codes as float, with the missing sentinel turned into real NaN.

    LightGBM handles NaN natively as "missing". `.fillna(0.0)` does NOT do this
    job and is worse than it looks: the served split carries no NaNs at all, so
    the fill never fires, and every sentinel reaches the model as an ordinal
    below the lowest real bin, which is precisely what the note above forbids.

    NOTE this DIVERGES from hosted training on purpose. The hosted presets fill
    NaN with 0.0 and pass the sentinel through as an ordinary number (see
    futures_starter_hosted.py), so a model fit here and one fit hosted on "the
    same" recipe see different encodings and are not directly comparable.
    """
    x = df[cols].astype("float32")
    return x.mask(x == MISSING)


# =====================================================================
# 5. Train a LightGBM model
# =====================================================================
print("\nTraining LightGBM...")

# A NaN target means the row was uncomputable; it is never imputed, so drop
# those rows rather than filling them.
fit_df = fit_df.dropna(subset=[target_col])

model = lgb.LGBMRegressor(
    n_estimators=2000,
    learning_rate=0.01,
    max_depth=6,
    num_leaves=64,
    colsample_bytree=0.10,
    subsample=0.80,
    min_child_samples=500,
    reg_alpha=0.1,
    reg_lambda=1.0,
    random_state=42,
    verbose=-1,
)
model.fit(features_matrix(fit_df, feat_cols), fit_df[target_col])

# =====================================================================
# 6. Evaluate on the embargoed holdout
# =====================================================================
# Spearman rank correlation between predictions and the target, computed within
# each exped (Pearson on ranks is Spearman). A quick proxy for FIT, not FIT
# itself: FIT is a covariance (rank-gaussianized predictions with the centred target).
print("\nEvaluating on the embargoed holdout...")

holdout_df = holdout_df.dropna(subset=[target_col]).copy()
holdout_df["prediction"] = model.predict(features_matrix(holdout_df, feat_cols))
corr = holdout_df.groupby(EXPED_COL)[["prediction", target_col]].apply(
    lambda g: g["prediction"].rank().corr(g[target_col].rank())
).dropna()
print(f"  Mean Spearman:     {corr.mean():+.4f}")
print(f"  Std Spearman:      {corr.std():.4f}")
print(f"  % Positive:        {(corr > 0).mean():.1%}")
print(f"  Sharpe (Spearman): {corr.mean() / corr.std():.2f}")
# This model never saw the embargo or the holdout. That is the price of an
# honest score. To submit a model fit on all of history, refit on the whole of
# train once you are happy with this one.

# =====================================================================
# 7. Predict: the id is the PARQUET INDEX, not a column
# =====================================================================
# The downloaded parquet has NO 'id' column. The id every submit lane wants is
# the index (its name is 'id'), and its values are opaque strings like
# 'eiq_559073fecf705ae5'. Submit them VERBATIM. Renumbering them 0..N-1 produces
# a submission that matches ZERO rows.
#
# The round lane takes values in [0, 1] and refuses anything outside it, NaN or
# infinite. Scoring is rank-based, so rank-normalising costs nothing. Ranking
# within each exped keeps the practice frame on the same footing.
raw = pd.Series(model.predict(features_matrix(scored, feat_cols)), index=scored.index)
predictions = pd.DataFrame(
    {"prediction": raw.groupby(scored[EXPED_COL]).rank(pct=True)},
    index=pd.Index(scored.index, name="id"),
)
assert predictions.index.is_unique, "duplicate ids"
assert predictions["prediction"].between(0, 1).all(), "predictions outside [0, 1] or NaN"
assert len(predictions) == len(scored), "row count must match the served split"

pred_path = OUTPUT_DIR / "baseline_predictions.parquet"
predictions.to_parquet(pred_path)
predictions.to_csv(pred_path.with_suffix(".csv"))
print(f"\nPredicted {len(predictions):,} rows; first id: {predictions.index[0]!r}")


# =====================================================================
# 8. Pickle the model, for auto-submit
# =====================================================================
# Auto-submit is the recommended path: upload_model(model_id, path) with a
# cloudpickled predict(live_features) that passes the sandbox predict and the
# structural gate, with auto_submit on (create_model turns it on by default),
# and the platform runs it and submits shortly after each round opens. Check
# get_models' lane_active field (and lane_note) to confirm the hosted run is active;
# a submission you make yourself always wins over it.
#
# If you do upload one: return a SINGLE-COLUMN DataFrame indexed by id with
# every value in [0, 1], use cloudpickle.dump (never pickle.dump), and select
# the features BY NAME so the artifact survives a change to the served columns.
def build_predict(fitted, columns, missing):
    # `missing` is closed over by value so the pickled artifact carries the
    # sentinel with it, instead of depending on a global that only exists here.
    def predict(live_features, live_benchmark_models=None):
        x = live_features.reindex(columns=columns).astype("float32")
        # A column the served split does not carry reindexes to real NaN, which
        # is the honest encoding for "absent" and what LightGBM already expects.
        x = x.mask(x == missing)
        raw = pd.Series(fitted.predict(x), index=live_features.index)
        return raw.rank(pct=True).to_frame("prediction")

    return predict


model_path = OUTPUT_DIR / "baseline_model.pkl"
with open(model_path, "wb") as fh:
    cloudpickle.dump(build_predict(model, feat_cols, MISSING), fh)
print(f"Wrote {pred_path.name} and {model_path.name}")

# =====================================================================
# 9. Submit down the CORRECT lane
# =====================================================================
# The lane follows the ROWS YOU ARE HOLDING (`scored_split` above), never a
# clock read taken after they were downloaded. The two splits are disjoint id
# namespaces: a frame sent down the other lane is accepted and then fails on
# zero id overlap, costing you the upload.
#
# A model must EXIST before you can submit for it; the platform never
# auto-creates one. create_model(name=...) is idempotent WITH a name: a 409 for
# a name you own comes back as the existing record (status="already_exists").
# Keep the response's `id`: that is the stable model_id. Model names are public
# on the leaderboard, so pick one that does not describe your recipe.
MODEL_NAME = os.environ.get("EIQ_MODEL_NAME", "starter-baseline")
try:
    MODEL_ID = client.create_model(name=MODEL_NAME)["id"]
    print(f"\nModel {MODEL_NAME!r} -> {MODEL_ID}")
except Exception as exc:  # noqa: BLE001
    bail("create_model", exc)

if scored_split == "live":
    # Re-read the round IMMEDIATELY before submitting, as a GUARD: it answers
    # "are the rows I am holding still submittable?", never "which lane?".
    try:
        now = read_round()
    except Exception as exc:  # noqa: BLE001
        bail("get_current_round (pre-submit re-read)", exc)
    if now.get("submission_window") != "open" or now.get("exped") != scored_exped:
        print(f"\nThese rows are round {scored_exped!r}, which is no longer open "
              f"(now: {now.get('exped')!r}, window {now.get('submission_window')!r}).")
        print("An upload now would not be scored. Re-run when the next round opens.")
        sys.exit(1)

    # The round lane takes an {id: prediction} dict covering the round's rows
    # exactly. Pass the exped explicitly (never the literal "current").
    preds = predictions["prediction"].to_dict()
    check = client.validate_submission(preds, exped=scored_exped)   # free, writes nothing
    print(f"validate_submission: {check}")
    if check.get("issues"):
        print("Fix the issues above before submitting.")
        sys.exit(1)
    # One submission per model per round: re-running this while the window is
    # open REPLACES the earlier entry (the response says `replaced`).
    try:
        result = client.submit_futures_predictions(MODEL_ID, preds, exped=scored_exped)
    except Exception as exc:  # noqa: BLE001
        bail("submit_futures_predictions", exc)
    print(f"Submitted to round {scored_exped}: {result}")
    status = client.get_submission_status(tournament="futures", round=scored_exped,
                                          model_id=MODEL_ID)
    print(f"get_submission_status: {status}")
else:
    print("\nNo round window is open: these are practice-board rows (display-only).")
    try:
        result = client.submit_validation_diagnostics(
            MODEL_ID, predictions.reset_index(), target=target_col,
        )
    except Exception as exc:  # noqa: BLE001
        bail("submit_validation_diagnostics", exc)
    print(f"Accepted on the practice board: {result}")

# =====================================================================
# 10. Where your score shows up
# =====================================================================
print("\nNext:")
print("  client.get_leaderboard():            the round board")
print("  client.get_scores(...):              your scores once the round resolves")
print("  client.get_validation_diagnostics(): the practice board result")
print("\nScores rank on a weighted blend of FIT, UNQ and INOV. Call explain_scoring")
print("for the live weights and do not assume which term dominates.")
