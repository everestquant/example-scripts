#!/usr/bin/env python3
"""
Everesteer Futures Tournament, Hosted-Training Starter

A LightGBM baseline fitted on Everesteer's servers with the platform `train`
call, then checked and packaged by THIS script. The server is used as the
engine that does the fitting, and nothing else: every number you act on and
every file you upload is produced here, from your own code.

  - The fit stops before your holdout. A `train` job fits the whole train
    split unless told otherwise, which would put your holdout inside the fit.
    `train_filter` ends it at the first exped of the embargo instead.
  - The score is computed here, on that holdout, not read off the job. The
    job's own CV numbers are measured differently and its UNQ is an estimate
    against a proxy, so they are not what the board will say.
  - The model is wrapped here. What a job hands back has not always been the
    one shape the upload gate accepts, so this script never uses it as-is.

A CPU LightGBM job costs a few cents.

It then submits down the correct lane: to today's round (one round each
weekday, Monday to Friday) if its window is open, to the practice board if not.

This produces:
  - hosted_model.pkl:              the model file the job returned, as downloaded
  - hosted_model_callable.pkl:     the wrapped predict() callable. OPTIONAL: only
                                   for upload_model; a submission is just predictions
  - hosted_predictions.parquet:    predictions file (id + prediction)

Usage:
    pip install "everestapi>=0.3.38" lightgbm pandas pyarrow cloudpickle
    export EIQ_API_KEY=...                 # from onboarding
    export EIQ_BASE_URL=https://api.everesteer.ai
    python himalayas/futures_starter_hosted.py
"""

from __future__ import annotations

import os
import pickle

import cloudpickle
import pandas as pd
from everestapi import EverestAPI

client = EverestAPI(
    api_key=os.environ["EIQ_API_KEY"],
    base_url=os.environ.get("EIQ_BASE_URL", "https://api.everesteer.ai"),
    tournament="futures",
)

HOLDOUT_EXPEDS = 100   # the tail of train, kept out of the fit and scored here
EMBARGO = 20           # expeds dropped in front of the holdout. A wide round number:
                       # the target's horizon is not published, so err wide.
EXPED = "exped"

# =====================================================================
# 1. Check the budget
# =====================================================================
credits = client.get_compute_credits()
available = credits.get("available_cents", 0)
print(f"Compute available: ${available / 100:.2f}")
if available <= 0:
    raise SystemExit(
        "No spendable compute balance: top up, or use himalayas/futures_starter.py "
        "to train locally instead."
    )

# =====================================================================
# 2. Carve the holdout, and work out where the fit has to stop
# =====================================================================
# The graded column comes from the schema: it differs between datasets and is
# not necessarily the first entry in `targets`.
schema = client.get_dataset_schema() or {}
TARGET = schema.get("primary_target")
if not TARGET:
    raise SystemExit("The schema declares no primary_target - refusing to guess.")

train = pd.read_parquet(client.download_dataset(split="train"))
# train's exped tokens are zero-padded, so their string order is their time order,
# which is also the order the server's cutoff compares on.
ordered = sorted(train[EXPED].unique())
holdout_expeds = set(ordered[-HOLDOUT_EXPEDS:])
FIRST_EMBARGO_EXPED = ordered[-(HOLDOUT_EXPEDS + EMBARGO)]
holdout = train[train[EXPED].isin(holdout_expeds)].dropna(subset=[TARGET])
print(f"Holdout: last {HOLDOUT_EXPEDS} expeds ({len(holdout):,} rows). "
      f"The fit stops before {FIRST_EMBARGO_EXPED}, leaving a {EMBARGO}-exped embargo.")

# =====================================================================
# 3. Launch the hosted training job, fenced off from the holdout
# =====================================================================
# gpu="CPU" is the cheapest tier and the right one for tree models; the default
# is T4. features="all" is passed explicitly because the default is "small",
# which on a dataset that publishes a single set is an alphabetical prefix of
# the feature list rather than a curated subset.
#
# No `target=`: the platform trains on whichever column the dataset declares as
# graded. Pass `target=` only when you deliberately want an AUXILIARY target.
print("Launching hosted train (LightGBM, CPU)...")
job = client.train(
    model="lightgbm",
    features="all",
    gpu="CPU",
    train_filter={"exped": {"cutoff_lt": FIRST_EMBARGO_EXPED}},
    params={
        "n_estimators": 2000,
        "learning_rate": 0.01,
        "max_depth": 6,
        "num_leaves": 64,
        "colsample_bytree": 0.10,
        "subsample": 0.80,
        "min_child_samples": 500,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "seed": 42,
    },
)
job_id = job["job_id"] if "job_id" in job else job["id"]
print(f"  Job {job_id} submitted. Polling (a CPU baseline takes a few minutes)...")

status = client.wait_for_job(job_id, timeout=3600)
if status["status"] != "completed":
    raise SystemExit(
        f"Job ended {status['status']}: {status.get('error_message')} "
        f". See client.get_job_log({job_id!r}) for the lifecycle trail."
    )
# The job also reports its own CV metrics under status["metrics"]. They are not
# used here: they are measured inside the job's folds, and its UNQ is an
# estimate against a proxy. The score that decides anything is step 5's.

# =====================================================================
# 4. Download the model, and make one function that scores a frame
# =====================================================================
pkl_path = client.download_model(job_id, "hosted_model.pkl")
# Safe to unpickle: this is YOUR OWN model artifact from YOUR authenticated
# train job (presigned, ownership-gated download): not third-party data.
with open(pkl_path, "rb") as f:
    fitted = pickle.load(f)

# The manifest is the record of what the model was fit on: the ordered feature
# columns and the preprocessing the trainer applied. Reproduce that preprocessing
# exactly. In particular, do NOT turn the missing value (-1) into NaN here: the
# trainer fed -1 to the model as an ordinary value, so the model learned it that
# way, and changing it at predict time would hand the model inputs it never saw.
manifest = status.get("feature_manifest") or {}
FEATURES = manifest["features"]
FILL = float(manifest.get("fill_value", 0.0))


def make_scorer(model, columns, fill):
    """Score a frame, whichever shape of artifact the job returned.

    A bare estimator takes a positional array, so columns are selected by name
    and in the manifest's order. A callable takes the frame and returns a
    one-column frame. `columns` and `fill` are bound by value so the pickled
    wrapper in step 7 carries them with it.
    """
    if hasattr(model, "predict"):
        def score(frame):
            arr = frame[columns].fillna(fill).to_numpy("float32")
            return pd.Series(model.predict(arr), index=frame.index)
    elif callable(model):
        def score(frame):
            out = model(frame)
            out = out.iloc[:, 0] if isinstance(out, pd.DataFrame) else pd.Series(out)
            return pd.Series(out.to_numpy(), index=frame.index)
    else:
        raise SystemExit(f"Unrecognised artifact type: {type(model).__qualname__}")
    return score


score = make_scorer(fitted, FEATURES, FILL)
print(f"Artifact: {type(fitted).__qualname__} "
      f"({'estimator' if hasattr(fitted, 'predict') else 'callable'}), {len(FEATURES)} features")

# =====================================================================
# 5. Score it on the holdout, here
# =====================================================================
# Spearman rank correlation between predictions and the target, computed within
# each exped (Pearson on ranks is Spearman). A quick proxy for FIT, not FIT
# itself: FIT is a covariance (rank-gaussianized predictions with the centred target).
def per_exped_corr(frame, pred_col):
    return frame.groupby(EXPED)[[pred_col, TARGET]].apply(
        lambda g: g[pred_col].rank().corr(g[TARGET].rank())
    ).dropna()


holdout = holdout.assign(prediction=score(holdout))
corr = per_exped_corr(holdout, "prediction")
print(f"Holdout Spearman {corr.mean():+.4f} | std {corr.std():.4f} | "
      f"sharpe {corr.mean() / corr.std():.2f} | {(corr > 0).mean():.0%} of expeds positive")

# UNQ is measured against this benchmark model, so it is the bar to beat. Score
# it on the same rows, the same way, so the comparison is like-for-like. A row is the
# same row only when its id AND its exped agree: a benchmark built from an older
# train file can share ids with this one on different expeds, and an id-only
# join would quietly score it against the wrong rows.
try:
    bench = pd.read_parquet(client.download_benchmark("futures", "train"))
    bench_mean = pd.DataFrame({"benchmark": bench.drop(columns=[EXPED]).mean(axis=1),
                               "bench_exped": bench[EXPED]})
    rows = holdout.join(bench_mean, how="inner")
    rows = rows[rows["bench_exped"] == rows[EXPED]]
    if len(rows) < 0.5 * len(holdout):
        print(f"Benchmark comparison skipped: only {len(rows):,} of {len(holdout):,} holdout "
              "rows match on id and exped, so the benchmark was built from a different "
              "train file than the one served now.")
    else:
        print(f"Benchmark    {per_exped_corr(rows, 'benchmark').mean():+.4f} "
              f"on {len(rows):,} of {len(holdout):,} holdout rows")
except Exception as exc:  # noqa: BLE001, a comparison is useful but not required
    print(f"Benchmark comparison unavailable: {exc}")

# This model never saw the embargo or the holdout. That is the price of an
# honest score: about 2% of the history. To submit a model fit on all of it,
# launch a second job without train_filter once you are happy with this one.

# =====================================================================
# 6. Predict on the split that is scored right now
# =====================================================================
# Today's round is served as `live` while its window is open. Between rounds
# get_current_round says "closed" (an upload then is accepted but NOT scored)
# and it 404s when no round is active at all, so the practice board is used
# instead. `split_exped` records WHICH round these rows came from: step 8's
# lane follows the rows in hand, not a later clock read.
def read_round():
    try:
        return client.get_current_round(tournament="futures") or {}
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status_code", None) == 404:
            return {}
        raise


current = read_round()
split = "live" if current.get("submission_window") == "open" else "validation"
split_exped = current.get("exped") if split == "live" else None
print(f"Downloading the served {split} split and predicting locally...")
served = pd.read_parquet(client.download_dataset(split=split))
# The id IS the parquet index. The round lane wants values in [0, 1]; scoring is
# rank-based, so ranking within each exped costs nothing.
ranked = score(served).groupby(served[EXPED]).rank(pct=True)
submission = pd.DataFrame({"prediction": ranked.to_numpy()},
                          index=pd.Index(served.index, name="id"))
submission.to_parquet("hosted_predictions.parquet")
print(f"  {len(submission):,} predictions written to hosted_predictions.parquet")


# =====================================================================
# 7. Wrap the model in a cloudpickled predict() callable (OPTIONAL)
# =====================================================================
# A daily submission is just the predictions. The wrapper matters only for
# upload_model: with it uploaded, passing the sandbox predict and the structural
# gate, and auto_submit on (create_model turns it on by default), the platform
# runs it and submits shortly after each round opens. Check get_started's
# auto_run field to confirm the hosted run is active; a submission you make
# yourself always wins over it. The wrapper reuses step 4's scorer, so it selects features by
# name and applies the same preprocessing the model was fit with.
def build_predict(scorer):
    def predict(live_features, live_benchmark_models=None):
        return scorer(live_features).rank(pct=True).to_frame("prediction")

    return predict


upload_pkl = "hosted_model_callable.pkl"
with open(upload_pkl, "wb") as f:
    cloudpickle.dump(build_predict(score), f)

# =====================================================================
# 8. Submit with the lane THESE ROWS belong to. The splits are disjoint id
#    namespaces: a frame sent down the other lane is accepted and then
#    fails on zero id overlap. Several models at once: the
#    submit_futures_predictions_batch MCP TOOL (the Python client has no
#    batch method - loop this call).
# =====================================================================
# create_model is idempotent WHEN YOU PASS A NAME: a 409 for a name you already
# own resolves to the existing record with status="already_exists". Either way
# read the response's `id`. That is the stable model_id every submit call wants;
# `name` is a mutable, PUBLIC display label.
MODEL_NAME = "hosted-baseline"
MODEL_ID = client.create_model(name=MODEL_NAME)["id"]
print(f"Model {MODEL_NAME!r} -> {MODEL_ID}")

if split == "live":
    # Re-read the round as a GUARD: minutes passed while the job trained. It
    # answers whether these rows are still submittable, never which lane.
    now = read_round()
    if now.get("submission_window") != "open" or now.get("exped") != split_exped:
        raise SystemExit(
            f"These rows are round {split_exped!r}, which is no longer open (now "
            f"{now.get('exped')!r}, window {now.get('submission_window')!r}). An upload "
            "now would not be scored: re-run when the next round opens."
        )
    preds = submission["prediction"].to_dict()     # {id: prediction}, every live row
    check = client.validate_submission(preds, exped=split_exped)   # free, writes nothing
    if check.get("issues"):
        raise SystemExit(f"validate_submission reported issues: {check}")
    # One submission per model per round: re-running while the window is open
    # REPLACES the earlier entry (the response says `replaced`).
    result = client.submit_futures_predictions(MODEL_ID, preds, exped=split_exped)
    print(f"Submitted to round {split_exped}: {result}")
    print(client.get_submission_status(tournament="futures", round=split_exped,
                                       model_id=MODEL_ID))
    print("Scores resolve once the target realises (about 20 days): client.get_scores().")
else:
    print("No round window is open: submitting to the practice board (display-only).")
    result = client.submit_validation_diagnostics(
        MODEL_ID,
        "hosted_predictions.parquet",
        target=TARGET,
        wait=True,
    )
    print(f"Scored: {result}")
    print("Check it with client.get_validation_diagnostics(model_id=MODEL_ID).")
