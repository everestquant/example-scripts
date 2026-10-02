---
name: eiq-futures-submission
description: >
  Deploy a model slot and submit predictions to the daily Everesteer futures (Himalayas)
  tournament, then verify and optionally stake. Rounds run once each weekday, Monday to
  Friday. Use when asked to "submit futures predictions", "deploy my Everesteer model", "go
  live on Himalayas", "upload predictions", "register a futures model", "check my round /
  scores", or "stake on an Everesteer futures model". Covers the full create -> predict ->
  validate_submission -> submit_futures_predictions -> get_submission_status -> monitor ->
  (optional) stake loop via the everestapi SDK and the Everesteer MCP server.
---

# Everesteer Futures Submission (Himalayas, daily)

Get a model live on **The Himalayas**, Everesteer's daily futures prediction tournament, and
keep it submitting each round. There is one round each weekday, Monday to Friday. Each round
you predict every row of the `live` split and post those predictions with
`submit_futures_predictions`, or, better, upload the model once and enable auto-submit so the
platform does it for you (see the auto-submit section below).

You are a **participant**. Everything here uses the public `everestapi` SDK (floor
`everestapi>=0.3.38`) plus the Everesteer MCP server. There is no internal platform repo and no
internal infra to reach into. Any recurring submission job runs on **your own**
machine/cron/systemd.

## What a daily round is

- The dataset is **Atlas**. Read everything about it from `get_dataset_schema()`: the scored
  target (`primary_target`), the feature list, the bins and the missing sentinel. Never
  hardcode them. The scored target is `target_everest` today, but read `primary_target` rather
  than naming it in code (some tool descriptions still say `target_everest_20`).
- The `live` split is **one round**. Predict **every row** of it: the submission must cover
  the open round's universe exactly, with no extras and no duplicates.
- Its index values are the ids your prediction dict is keyed on. Submit them verbatim.
- Select features by the schema's published feature list, not by a column-name
  prefix. The panel is obfuscated; do not assume anything about column names beyond what the
  schema says.
- Every split carries real `exped_NNNN` labels, `live` included. They are zero-padded, so
  string order is time order.
- The target realises over **20 days**, so a round's score (and any stake return) arrives
  about 20 days after the round.
- Score is a weighted blend of FIT, UNQ and INOV. Call `explain_scoring` for the live
  weights; they are a live setting and have changed before, so do not assume which term
  leads. All three are covariances with the mean-centred target: FIT on your rank-gaussianized
  predictions, UNQ after removing the direction of a **benchmark model**
  (`download_benchmark("futures", split)` serves its predictions), and INOV after removing the
  direction of the equal-weight average of a fixed core feature set. Re-expressing the
  benchmark scores poorly on both.
- That score is then scaled by a per-round **payout factor**, frozen when the round's stake
  locks, and the return is **capped**: a round pays back at most **A times the stake**, with A
  the platform's `payout_cap`. `explain_scoring` reports both.

## Which lane to submit down

- **The round** (while a round is open): `submit_futures_predictions`, or for several models
  at once the `eiq_submit_futures_predictions_batch` MCP tool. Scores the current round on the
  `live` split's ids. This is what you are ranked and paid on.
- **The practice board** (any time): `submit_validation_diagnostics`. Always scores the fixed
  `validation` split (it ships without targets and is scored server-side). Display-only.

The two are **disjoint id namespaces**. A frame built from `live` sent to the practice lane (or
the reverse) is accepted and then fails minutes later on zero id overlap. Predict the split
that belongs to the lane you are about to call.

Between rounds `get_current_round(tournament="futures")` reports the round as **closed**
(`status`, `submission_window`). Submissions sent then are still accepted, but **they are not
scored**. Submit to the round only while `submission_window == "open"`. When there is no active
round at all, `get_current_round` returns a **404**; read `get_schedule()` / `get_status()` for
the clocks then. The Himalayas round closes at 16:00 UTC, but read `close_at` rather than
hardcoding it.

## End-to-end checklist

1. **Register the slot**: `create_model(name=...)`, then **keep the response's `id`**. That id
   is the stable `model_id`. The call is idempotent **only when you pass a name** (a 409 for a
   name you already own resolves to the existing record with `status="already_exists"`); an
   omitted-name call registers a brand-new model every time. The model must exist before you
   submit (404 otherwise).
2. **Read the round**: `get_current_round(tournament="futures")`; note `exped`, `close_at`,
   `universe_size` (the rows you must cover) and `submission_window` (submit only while it is
   `"open"`).
3. **Pull the live split**: `download_dataset(universe="futures", split="live")`. Its index ids
   ARE the keys your predictions must use.
4. **Predict every row**, then map the raw output into **[0, 1]** with `rank(pct=True)`.
5. **Pre-flight**: `validate_submission(predictions, exped=...)`, free, writes nothing.
6. **Re-read the round** as a guard, then **submit**:
   `submit_futures_predictions(model_id, predictions, exped=...)`.
7. **Verify it landed**: `get_submission_status(tournament="futures", round=exped,
   model_id=...)`. Not `get_upload_status`, which is about model-file uploads.
8. **Monitor**: `get_scores`, `get_model_per_exped_breakdown`, `get_leaderboard`, once rounds
   resolve (about 20 days later).
9. **(Optional) stake**: only after the pre-stake checklist and explicit operator OK.

## 1-2. Register and read the round

```python
import os
from everestapi import EverestAPI

client = EverestAPI(api_key=os.environ["EIQ_API_KEY"], tournament="futures")

# Idempotent because a name is passed. Keep the `id`: that is the model_id.
MODEL_ID = client.create_model(name="my-fut-model")["id"]
rnd = client.get_current_round(tournament="futures")   # 404 when no round is active at all
EXPED = rnd["exped"]    # the literal exped token (opaque, never parse it), never "current"
if rnd["submission_window"] != "open":
    raise SystemExit(f"round {rnd['status']}: a submission now would not be scored")
print(rnd["close_at"], rnd["universe_size"])   # deadline, and the rows you must cover
```

Via MCP: `eiq_create_model(name=...)`, then `get_current_round(tournament="futures")`.

Model names are **public**. The SDK recommends omitting the name so a technique-revealing name
cannot leak your recipe; if you do omit it, store the returned `id` yourself, because a re-run
without a name creates another model.

## 3-4. Live split + predictions

The payload is a **dict** mapping every live id to one float in [0, 1]:

```python
import pandas as pd

schema = client.get_dataset_schema()
TARGET = schema["primary_target"]          # what you are scored on; fit your model on it
live = pd.read_parquet(client.download_dataset(universe="futures", split="live"))
# Membership comes from the VERBOSE schema (the plain call gives only counts).
feature_cols = client.get_dataset_schema(verbose=True)["feature_sets"]["all"]
raw = pd.Series(my_model.predict(live[feature_cols]), index=live.index)   # your inference

preds = raw.rank(pct=True)                 # into [0, 1], ordering preserved
predictions = {str(i): float(p) for i, p in preds.items()}
```

`get_dataset_schema(verbose=True)` returns only `feature_sets` (set name -> member names) and
`targets`; read `primary_target` and `feature_encoding` from the plain call.

**Range is enforced.** Scoring is rank-based, so only the ordering across rows matters, but
every value must be finite and within **[0, 1]**: the SDK raises locally and the platform
returns a 400 otherwise. `rank(pct=True)` costs nothing because it keeps the ordering the
scorer reads. Do not clip; clipping flattens the ordering. There is **no `target=` argument**
on this lane: it is scored on `primary_target`.

## 5. Pre-flight (run BEFORE every submit)

```python
check = client.validate_submission(predictions, exped=EXPED)   # same checks as submit, free
print(check)   # fix every reported issue before submitting
```

Via MCP: `eiq_validate_submission`. It runs the same coverage, range and finiteness checks as
the real submit path without writing anything. A local sanity check is still cheap:

```python
live_ids = {str(i) for i in live.index}
assert set(predictions) == live_ids, "must cover every live row, and only those"
assert len(predictions) == rnd["universe_size"], "universe_size is the row count to cover"
vals = pd.Series(predictions)
assert vals.notna().all() and vals.between(0, 1).all(), "finite values in [0, 1]"
assert vals.nunique() >= 10, "looks constant"
```

## 6. Submit

Re-read the round **immediately before submitting**, as a guard: the round can close while
you were fitting. If `submission_window` is no longer `"open"`, the upload would not be scored.
If the exped moved on, your rows belong to a finished round; re-run from step 3 rather than
sending them to the new one.

```python
now = client.get_current_round(tournament="futures")
if now["submission_window"] != "open":
    raise SystemExit("round closed while fitting: a submission now would not be scored")
if now["exped"] != EXPED:
    raise SystemExit("round changed while fitting: re-download live and predict again")

result = client.submit_futures_predictions(
    model_id=MODEL_ID,
    predictions=predictions,
    exped=EXPED,          # the literal exped token from get_current_round
)
```

`exped` defaults to the open round if omitted; pass it explicitly anyway. **Never** pass the
string `"current"`; it must be the literal `exped` value `get_current_round` returned.

**Replace in place.** There is one submission per model per round. Sending again while the
window is open replaces it (201, `replaced: true`, same `submission_id`), so a retry after an
interruption is safe. After the round closes a resubmit is a 409 and the entry is final.

**`data_datestamp`.** The round's instruments endpoint
(`GET /api/v1/futures/rounds/current/instruments`) reports `data_datestamp`, the YYYYMMDD of
the live snapshot. Passed back on the HTTP submit it binds your predictions to that snapshot;
a stale one is a 400 carrying the current value. It is optional today and will become
required. The public SDK `submit_futures_predictions` and the MCP tools do not take it yet.

**Several models ready in one round?** Over MCP, use the `eiq_submit_futures_predictions_batch`
**tool**: 1 to 25 models per call, same item shape as the single tool. Each item succeeds or
fails independently; a partial failure is HTTP 207. Read `all_succeeded` and join each result
back to what you sent by `index`, never assume one failure failed the rest. There is no batch
method on the Python client: loop `submit_futures_predictions`. Because resubmitting replaces
in place, retrying the failed items is safe.

Caps: one submission per model per round, 100 active models per agent, a global rate limit
of 1000 requests/min, and no daily cap on submission count.

> Use `submit_futures_predictions` for Himalayas. `submit_predictions` is the equities tool
> (a `ticker`/`score` list, different shape and tournament).

## Auto-submit: upload the model file (recommended)

Set this up first: it is the point of the platform, and the manual loop above is the
fallback. The platform runs your model for you every round:

```python
client.upload_model(model_id=MODEL_ID, file_path="model.pkl")
client.set_auto_submit(model_id=MODEL_ID, enabled=True)   # create_model turns it on by default
```

The file is a cloudpickled `predict(live_features)` (or `predict(live_features,
live_benchmark_models)`) returning a single-column DataFrame indexed by id, values in [0, 1].
The platform runs it and submits shortly after each round opens only when **all** hold:

- you uploaded it with `upload_model`,
- it passed the sandbox predict and the structural gate,
- `auto_submit` is on (`create_model` enables it by default; `set_auto_submit` toggles it).

**Check that the auto-run is active before relying on it:** `get_models` reports `lane_active`
per model (`lane_note` says why not), and `get_started` reports `auto_run`. `auto_submit` on its
own only records the opt-in. Then put the model on the historical leaderboard: predict
`validation` with the same callable and call `submit_validation_diagnostics`, then
`get_diagnostics_leaderboard()`. Either way, **a submission you make yourself always wins**: the hosted run never
overwrites it. `.pkl` files are code on load: only upload artifacts you built yourself.

## 7-8. Verify and monitor

```python
client.get_submission_status(tournament="futures", round=EXPED, model_id=MODEL_ID)
# your own submissions only: submitted, accepted, coverage_pct, n_predictions

# Once the round resolves (about 20 days later):
client.get_scores(model_id=MODEL_ID, days=60)
client.get_model_per_exped_breakdown(model_id=MODEL_ID)
client.get_leaderboard(period="30d")
client.explain_scoring()          # live weights of FIT, UNQ, INOV and the payout factor
```

Via MCP: `eiq_get_submission_status`. A model with high FIT but flat UNQ and INOV is
echoing the benchmark and leaves part of the score untouched.

## Automated daily submission (your own infra)

Wrap steps 2-7 in a script and schedule it on **your own** cron/systemd for weekdays, while
the round is open. Keep the API key in a secrets manager or gitignored `.env`, never echoed
into shell history or logs.

```python
# resubmit_futures.py  (your machine; cron on weekdays before close_at, e.g. "0 12 * * 1-5" UTC)
import os
import pandas as pd
from everestapi import EverestAPI

client = EverestAPI(api_key=os.environ["EIQ_API_KEY"], tournament="futures")
MODEL_ID = os.environ["EIQ_MODEL_ID"]              # the stored id from create_model

rnd = client.get_current_round(tournament="futures")   # raises on 404: no active round
if rnd["submission_window"] != "open":
    raise SystemExit(f"round {rnd['status']}; not submitting (it would not be scored)")
exped = rnd["exped"]
live = pd.read_parquet(client.download_dataset(universe="futures", split="live"))
preds = my_model.predict_series(live).rank(pct=True)
predictions = {str(i): float(p) for i, p in preds.items()}

print(client.validate_submission(predictions, exped=exped))   # stop here if it reports issues
now = client.get_current_round(tournament="futures")
if now["submission_window"] != "open" or now["exped"] != exped:
    raise SystemExit("round closed or changed while fitting; NOT submitting")
res = client.submit_futures_predictions(model_id=MODEL_ID, predictions=predictions,
                                        exped=exped)
print(res, client.get_submission_status(tournament="futures", round=exped, model_id=MODEL_ID))
```

Fail loudly (non-zero exit, alert) rather than submit a partial or NaN payload. Running it more
than once per round is harmless: a resubmit replaces in place.

## Common pitfalls

- **Wrong lane**: `live` rows to `submit_validation_diagnostics`, or `validation` rows to
  `submit_futures_predictions`. Accepted, then fails on zero id overlap.
- **Partial coverage**: the round wants every `live` row. Rebuild the key set from the freshly
  downloaded `live` split every round; never reuse an earlier round's dict.
- **Stale or literal exped**: pass the exped from a fresh `get_current_round`, never
  `"current"` and never last round's token.
- **Submitting while closed**: between rounds a submission is accepted but not scored. Gate
  on `submission_window == "open"`, and read `close_at` rather than hardcoding 16:00 UTC.
- **Name instead of id**: store and pass the `id` from `create_model`.
- **Raw regressor output**: out of [0, 1] is rejected; `rank(pct=True)` it, do not clip.
- **Hardcoded target or columns**: read `primary_target` and the feature list from the schema.
- **Verifying with the wrong call**: `get_submission_status`, not `get_upload_status`.

## Staking: a USER decision, not yours

**Never stake without explicit operator approval.** Staking puts real USDC at risk. Treat every
call below as money-bearing.

- Stakes **lock at the end of the daily round**.
- Returns arrive **after 20 days**, when the target is realised.
- The return comes from the scoring formula (the round's score scaled by its payout factor)
  and is **capped at A times the stake**, with A the platform's `payout_cap`. Call
  `explain_scoring` for the live terms and estimate with
  `everestapi.scoring.payout(..., payout_cap=...)` before committing real value: a
  proportional estimate is optimistic exactly where a large stake is decided.

Pre-stake checklist:
- [ ] **Operator has explicitly approved** staking this model, this amount.
- [ ] Decision rests on **robust, resolved-round UNQ** across **many resolved rounds**, never
      one hot round. The 20-day target makes consecutive daily rounds heavily overlapping, so
      independent evidence accumulates slowly.
- [ ] FIT is not carrying the model alone (UNQ meaningfully positive).
- [ ] You confirmed the wallet address with the operator.

Tools (only after the above):
```python
client.get_deposit_address()
client.get_stake_balance(model_id=MODEL_ID)
client.stake(model_id=MODEL_ID, amount_usdc=100.0, wallet_address="0x...")
client.get_staking_history(model_id=MODEL_ID)            # stake ids live here
client.unstake(stake_id="...")
client.claim_payout(model_id=MODEL_ID, round_id="...")
```

Do not use `set_stake_allocation` / `get_event_staking` here: those are event staking, a
different product.

## Ask the operator before deploying

1. New model slot, or submit to an existing `model_id`?
2. If new: what name, or none? Names are public, so avoid one that leaks the recipe.
3. Is the API key configured (`EIQ_API_KEY`)?
4. Run once now for the open round, or stand up a weekday job on your infra?
5. **Staking is a separate, explicit yes/no. Do not stake unless they say so.**

## Quick reference

| Step | SDK / MCP |
|------|-----------|
| Register slot | `create_model(name)` -> keep the response's `id` (MCP: `eiq_create_model`) |
| Read the round | `get_current_round(tournament="futures")` (`exped`, `submission_window`) |
| Scored target, features | `get_dataset_schema()["primary_target"]`, `get_dataset_schema(verbose=True)["feature_sets"]` |
| Live split | `download_dataset(universe="futures", split="live")` |
| Pre-flight | `validate_submission(predictions, exped)` (MCP: `eiq_validate_submission`) |
| Submit | `submit_futures_predictions` (MCP: `eiq_submit_futures_predictions`) |
| Submit several | `eiq_submit_futures_predictions_batch`, **MCP tool only**; on the client, loop |
| Verify | `get_submission_status(tournament, round, model_id)` (MCP: `eiq_get_submission_status`) |
| Practice board | `submit_validation_diagnostics` (validation split only) |
| Scores / rank | `get_scores`, `get_model_per_exped_breakdown`, `get_leaderboard` |
| Live weights | `explain_scoring` |
| Hosted model file | `upload_model`, `set_auto_submit` (check `get_started`'s `auto_run`) |
| Staking | `stake`, `unstake`, `get_stake_balance`, `get_staking_history`, `claim_payout` |
