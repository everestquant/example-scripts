#!/usr/bin/env python3
"""
Everesteer Futures Tournament — Starter Example

Train a LightGBM model on the Himalayas (Futures) tournament dataset,
evaluate on validation, and save predictions for submission.

This produces:
  - baseline_model.pkl     — serialized model (uploadable via API)
  - example_predictions.parquet — predictions file (submittable via API)

Usage:
    pip install lightgbm pandas pyarrow scikit-learn
    python examples/futures_starter.py
"""

from __future__ import annotations

import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# =====================================================================
# 1. Load the dataset
# =====================================================================
DATASET_DIR = Path("data/datasets/v0/futures")

print("Loading dataset...")
train = pd.read_parquet(DATASET_DIR / "train.parquet")
val_full = pd.read_parquet(DATASET_DIR / "validation.parquet")
live = pd.read_parquet(DATASET_DIR / "live.parquet")

# The validation file contains both validation and test splits
val = val_full[val_full["data_type"] == "validation"]
test = val_full[val_full["data_type"] == "test"]

# Exped column (each trading day = one expedition)
EXPED_COL = "exped"

# Feature and target columns
feat_cols = sorted([c for c in train.columns if c.startswith("feature_")])
target_col = "target"  # primary target (target_everest_5)

print(f"  Train:      {len(train):>8,} rows  |  {train[EXPED_COL].nunique()} expeds")
print(f"  Validation: {len(val):>8,} rows  |  {val[EXPED_COL].nunique()} expeds")
print(f"  Test:       {len(test):>8,} rows  |  {test[EXPED_COL].nunique()} expeds")
print(f"  Live:       {len(live):>8,} rows  |  {live[EXPED_COL].nunique()} expeds")
print(f"  Features:   {len(feat_cols)}")

# =====================================================================
# 2. Train a LightGBM model
# =====================================================================
print("\nTraining LightGBM...")

X_train = train[feat_cols].fillna(0).values
y_train = train[target_col].fillna(0).values

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
model.fit(X_train, y_train)

# =====================================================================
# 3. Evaluate on validation
# =====================================================================
print("\nEvaluating on validation...")

X_val = val[feat_cols].fillna(0).values
val_preds = model.predict(X_val)

# Per-exped Spearman correlation (the tournament's core metric)
corrs = []
val_with_preds = val[[EXPED_COL, target_col]].copy()
val_with_preds["prediction"] = val_preds

for _, grp in val_with_preds.groupby(EXPED_COL):
    mask = grp[target_col].notna()
    if mask.sum() >= 5:
        rho, _ = spearmanr(grp.loc[mask, "prediction"], grp.loc[mask, target_col])
        if np.isfinite(rho):
            corrs.append(rho)

corr_arr = np.array(corrs)
print(f"  Mean CORR:     {corr_arr.mean():.4f}")
print(f"  Std CORR:      {corr_arr.std():.4f}")
print(f"  % Positive:    {(corr_arr > 0).mean() * 100:.1f}%")
print(f"  Sharpe (CORR): {corr_arr.mean() / corr_arr.std() * np.sqrt(252):.2f}")

# =====================================================================
# 4. Generate live predictions
# =====================================================================
print("\nGenerating live predictions...")

X_live = live[feat_cols].fillna(0).values
live_preds = model.predict(X_live)

# Format for submission: DataFrame with id index, exped + prediction columns
submission = pd.DataFrame(
    {EXPED_COL: live[EXPED_COL].values, "prediction": live_preds},
    index=live.index,
)
submission.index.name = "id"

# =====================================================================
# 5. Save model and predictions
# =====================================================================
OUTPUT_DIR = Path("examples")

# Save model as .pkl (uploadable via API)
model_path = OUTPUT_DIR / "baseline_model.pkl"
with open(model_path, "wb") as f:
    pickle.dump({"model": model, "feature_cols": feat_cols}, f)
print(f"\n  Model saved: {model_path} ({model_path.stat().st_size / 1024:.0f} KB)")

# Save predictions as .parquet (submittable via API)
pred_path = OUTPUT_DIR / "example_predictions.parquet"
submission.to_parquet(pred_path)
submission.to_csv(pred_path.with_suffix(".csv"))
print(f"  Predictions saved: {pred_path} ({len(submission)} rows)")

print("\nDone! Upload your model:")
print(f"  client.upload_model('my-model', '{model_path}')")
print("  client.submit_predictions('my-model', predictions=...)")
