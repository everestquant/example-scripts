---
name: eiq-futures-submission
description: >
  Deploy a model slot and submit per-chain predictions to the EIQ FUTURES (Himalayas)
  tournament, then verify and optionally stake. Use when asked to "submit futures
  predictions", "deploy my EIQ model", "go live on Himalayas", "upload predictions",
  "register a futures model", "check my round / scores", or "stake on an EIQ futures
  model". Covers the full create -> predict -> submit_futures_predictions -> verify ->
  monitor -> (optional) stake loop via the everestapi SDK and the EIQ MCP server.
---

# EIQ Futures Submission (Himalayas)

Get a model live on **The Himalayas**, EIQ's futures prediction tournament, and keep it
submitting each round. Unlike a packaged-artifact tournament, the default Himalayas flow
is **live prediction submission**: every round you score the live universe of futures
**chains** and post those scores with `submit_futures_predictions`. There is also an
optional artifact path (`upload_model`) if you would rather the platform run a trained
`.pkl` on your behalf each round.

You are a **participant**. Everything here uses the public `everestapi` SDK plus the EIQ
MCP server. There is no internal platform repo and no internal infra to reach into — any
recurring submission job runs on **your own** machine/cron/systemd.

## How Himalayas differs from a one-and-done upload

- The submission unit is a **score per chain (instrument id)** for the round's live
  universe — a real-valued prediction of forward ranking, one float per chain.
- The universe is futures **chains** grouped into **clusters** (eq, rates, energy, ags,
  fx, metals, volatility, …). Discover it with `get_universe`; never hardcode it.
- Rounds are **weekly** for futures and key off the live data snapshot, not a fixed
  wall clock. You must submit before the open round stops accepting.
- The scored target is `target_everest_20` (20-day forward return, rank-normalized).
- Payout formula (both tournaments): **`payout = 0.75·CORR + 2.25·AIMC`**, capped at
  **±25% of your stake** per round. AIMC (differentiation from the ai-model consensus)
  is what actually drives payout — duplicating the benchmark scores well on CORR but
  pays little.

## Two deployment paths

| Path | You do | Platform does | Choose when |
|------|--------|---------------|-------------|
| **Live submission** (default) | Generate scores each round, call `submit_futures_predictions` | Scores your posted predictions | You want full control / your model lives on your infra |
| **Artifact upload** | `upload_model` a trained `.pkl` once | Runs your `.pkl` on each round's live data automatically | You want hands-off, server-side inference (supported for futures/Himalayas; cadence is weekly) |

Both require a registered model slot first.

## End-to-end checklist

1. **Register the slot** — `create_model(name=...)`. The name becomes your `model_id`.
   Idempotent, so safe to re-run.
2. **Find the open round** — `get_current_round(tournament="futures")` /
   `get_schedule`; note the exped and that it is still accepting.
3. **Pull the live universe** — `download_dataset(universe="futures", split="live")`
   (and/or `get_universe`). The live parquet's index ids ARE the keys your prediction
   dict must use.
4. **Generate one score per chain** for the live universe.
5. **Validate locally** (see pre-submission checklist) — full coverage, no NaN, no dups.
6. **Submit** — `submit_futures_predictions(model_id, predictions, exped=...)`.
7. **Verify it landed** — `get_upload_status` / `get_round_diagnostics`.
8. **Monitor** — `get_scores`, `get_model_per_exped_breakdown`, `get_leaderboard`.
9. **(Optional) stake** — only after the pre-stake checklist and explicit operator OK.

## 1–2. Register and locate the round

```python
import os
from everestapi import EverestAPI

client = EverestAPI(api_key=os.environ["EIQ_API_KEY"], tournament="futures")

client.create_model(name="my-fut-model")          # idempotent; model_id == name
round_info = client.get_current_round()            # tournament="futures" from constructor
# round_info exposes the open exped + whether it is still accepting submissions
```

Via MCP: `create_model(name="my-fut-model")`, then
`get_current_round(tournament="futures")`.

## 3–4. Live universe + predictions

The prediction payload for futures is a **dict** mapping each live instrument id to a
single float for `target_everest_20`:

```python
import pandas as pd

live = pd.read_parquet(client.download_dataset(universe="futures", split="live"))
# the parquet index ids are the exact keys submit_futures_predictions expects
feature_cols = [c for c in live.columns if c.startswith("feature_")]
live["pred"] = my_model.predict(live[feature_cols])   # your inference

predictions = {str(idx): float(p) for idx, p in live["pred"].items()}
```

The MCP `submit_futures_predictions` tool takes the same shape:
```
predictions = {"<chain_id_a>": 0.42, "<chain_id_b>": -0.13, ...}   # one float per chain
```

**Score normalization.** Predictions are ranked per-exped before scoring, so the
absolute scale is irrelevant — only the *ordering* across chains matters. Submit any
finite real-valued score per chain; the platform handles cross-sectional ranking. There
is **no enforced `[-1,1]` range for futures** — arbitrary finite floats are accepted and
ranked. (The `[-1,1]` `ticker`/`score` constraint applies only to the equities
file-submit path `submit_predictions_file`, not to futures — do not clip.)

## 5. Pre-submission checklist (run BEFORE every submit)

```python
def check_futures_predictions(predictions: dict, live_ids: set) -> list[str]:
    errs = []
    pred_ids = set(predictions)
    missing = live_ids - pred_ids
    if missing:
        errs.append(f"missing {len(missing)} chains, e.g. {list(missing)[:5]}")
    extra = pred_ids - live_ids
    if extra:
        errs.append(f"{len(extra)} chains not in live universe, e.g. {list(extra)[:5]}")
    vals = list(predictions.values())
    nans = [k for k, v in predictions.items() if v != v]           # NaN
    if nans:
        errs.append(f"{len(nans)} NaN scores")
    infs = [k for k, v in predictions.items() if v in (float("inf"), float("-inf"))]
    if infs:
        errs.append(f"{len(infs)} non-finite scores")
    if len(set(vals)) < 10:
        errs.append(f"only {len(set(vals))} unique scores — looks constant")
    return errs

live_ids = set(str(i) for i in live.index)
errs = check_futures_predictions(predictions, live_ids)
assert not errs, errs
```

Cover **every** chain in the current live split, **only** those chains, no NaN/inf, no
duplicate keys (a dict prevents dups by construction — watch for them if you build the
payload from a list), and a real distribution of values.

## 6. Submit

```python
result = client.submit_futures_predictions(
    model_id="my-fut-model",
    predictions=predictions,
    exped=round_info["exped"],     # pass the LITERAL open exped, e.g. "exped_20260408"
)
```

`exped` defaults to the current round if omitted, but pass it explicitly so you never
post to the wrong window. **Never** pass the string `"current"` — it must be the literal
exped value from the live split / `get_current_round`.

> Use `submit_futures_predictions` for Himalayas. Do **not** use `submit_predictions`
> — that is the equities/Alps tool (it takes a `ticker`/`score` list, different shape
> and tournament). Mixing them is the most common deploy mistake.

## (Alt) Artifact upload path

If you prefer server-side inference instead of posting scores each round:

```python
client.upload_model(model_id="my-fut-model", file_path="model.pkl")
client.get_upload_status(model_id="my-fut-model")   # pending -> validating -> validated | error
```

An uploaded `.pkl` is run automatically each round on the futures/Himalayas live data
(weekly cadence) — this is the supported hands-off / server-side inference alternative to
calling `submit_futures_predictions` yourself. `.pkl` files are RCE-equivalent on load —
only build/upload artifacts you produced yourself, and never load a third-party `.pkl`
without isolating it first.

## 7–8. Verify and monitor

```python
client.get_upload_status(model_id="my-fut-model")          # submission accepted?
client.get_round_diagnostics(model_id="my-fut-model")      # this round's acceptance/coverage
client.run_validation_diagnostics(model_id="my-fut-model") # Sharpe, mean CORR/EAC, drawdown

# Once the round resolves (~20 trading days later for target_everest_20):
client.get_scores(model_id="my-fut-model", days=60)
client.get_model_per_exped_breakdown(model_id="my-fut-model")  # per-exped CORR + EAC series
client.get_leaderboard(period="30d")
```

Metrics to read: **AIMC** (AI Model Contribution — contribution beyond the live
stake-weighted ai-model consensus; the 2.25-weighted payout driver, and the live metric
your offline EAC optimization was chasing), **CORR** (rank agreement with realized 20-day
returns; the 0.75-weighted component), and **EAC** (Everest Alpha Contribution — your
unique signal beyond the static benchmark `eiq_minera_model`, same formula as AIMC but vs
the static benchmark; the primary experiment-selection metric and offline proxy for AIMC).
A model with high CORR but near-zero AIMC is echoing the benchmark and will pay little.

## Automated per-round submission (your own infra)

Wrap steps 2–8 in a script and schedule it on **your own** cron/systemd so it
regenerates predictions and resubmits each round. Keep the API key in a secrets
manager or gitignored `.env`, never echoed into shell history or logs.

```python
# resubmit_futures.py  (your machine; e.g. systemd timer or cron, weekly)
import os, pandas as pd
from everestapi import EverestAPI

client = EverestAPI(api_key=os.environ["EIQ_API_KEY"], tournament="futures")
rnd = client.get_current_round()
live = pd.read_parquet(client.download_dataset(universe="futures", split="live"))
preds = {str(i): float(p) for i, p in my_model.predict_series(live).items()}

errs = check_futures_predictions(preds, set(str(i) for i in live.index))
if errs:
    raise SystemExit(f"validation failed, NOT submitting: {errs}")

res = client.submit_futures_predictions(model_id="my-fut-model", predictions=preds,
                                     exped=rnd["exped"])
print("submitted", res)
```

Schedule it to fire while the round is open and to **fail loudly** (non-zero exit, alert)
rather than submit a partial/NaN payload.

## Common pitfalls (futures-specific)

- **Wrong submit tool** — `submit_predictions` (equities list) vs
  `submit_futures_predictions` (futures dict). Use the futures one for Himalayas.
- **Universe mismatch** — predicting chains that aren't in *this* round's live split, or
  missing some. Always rebuild the key set from the freshly downloaded live parquet;
  the universe changes as chains onboard/retire.
- **Stale exped** — submitting against last round's exped, or passing `"current"` as a
  literal string. Pull the open exped fresh and pass it.
- **Submitting after close** — the futures round keys off the data snapshot; if it has
  stopped accepting, your post silently does nothing useful. Check the round is open.
- **NaN / inf / constant scores** — usually a feature-join or inference bug; the
  pre-submission check catches these.
- **Assuming an equities `[-1,1]` clamp** — that range applies only to the equities
  *file* submit (`submit_predictions_file`); futures floats are arbitrary finite values
  ranked per-exped. Do not clip futures predictions to `[-1,1]`.

## Staking — a USER decision, not yours

**Never stake without explicit operator approval.** Staking puts real USDC at risk
(payout capped ±25% of stake per round). Treat all staking calls as money-bearing.

Pre-stake checklist:
- [ ] **Operator has explicitly approved** staking this model, this amount.
- [ ] Decision rests on **robust out-of-sample EAC** (the offline proxy for AIMC, with
      per-exped stability alongside; plus resolved-round AIMC where available) across
      **many resolved rounds**, never a single hot round. 20-day
      targets + weekly rounds means few independent observations accumulate — wait for a
      real track record.
- [ ] CORR is not carrying the model alone (AIMC meaningfully positive).
- [ ] Performance is stable across clusters, not one lucky asset class.
- [ ] You confirmed the wallet address and the staking network/contract status with the
      operator.

Tools (only after the above):
```python
client.get_stake_balance(model_id="my-fut-model")
client.stake_on_model(model_id="my-fut-model", amount_usdc=100.0, wallet_address="0x...")
client.claim_payout(model_id="my-fut-model", round_id="...")
client.unstake_from_model(model_id="my-fut-model", amount_usdc=100.0)
```

## Ask the operator before deploying

1. New model slot, or submit to an existing `model_id`?
2. If new — what name (it is permanent and becomes the `model_id`)?
3. Live-submission path or artifact upload?
4. Is the API key configured (`EIQ_API_KEY`)?
5. Run once now, or stand up a recurring per-round job on your infra?
6. **Staking is a separate, explicit yes/no — do not stake unless they say so.**

## Quick reference

| Step | SDK / MCP |
|------|-----------|
| Register slot | `create_model(name)` |
| Find round | `get_current_round(tournament="futures")`, `get_schedule` |
| Universe | `get_universe`, `download_dataset(universe="futures", split="live")` |
| Submit | `submit_futures_predictions(model_id, predictions={id: float}, exped)` |
| Verify | `get_upload_status`, `get_round_diagnostics` |
| Diagnostics | `run_validation_diagnostics`, `get_model_per_exped_breakdown` |
| Scores / rank | `get_scores`, `get_leaderboard` |
| Multipliers | `set_multipliers(model_id, corr_multiplier, aimc_multiplier)` |
| Artifact path | `upload_model`, `get_upload_status` |
| Staking | `get_stake_balance`, `stake_on_model`, `claim_payout`, `unstake_from_model` |
