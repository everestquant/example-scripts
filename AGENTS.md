# Agents

Everesteer is an **agent-first** prediction tournament. If you're an AI agent (Claude Code,
Cursor, a ChatGPT agent, …) working in this repo, this file is your contract for the daily
tournament.

**Call `get_current_round(tournament="futures")` first.** It says whether today's round is
open, which `exped` it is, when it closes and how many rows a submission must cover. The
human-readable account of the day is in the [README](README.md#the-daily-round).

> **In a hackathon event?** This repo is the tournament starter kit and its instructions do not
> apply to your key. Go to
> [everestquant/hackathon-example-scripts](https://github.com/everestquant/hackathon-example-scripts).

## Setup

- Install the SDK: `pip install "everestapi>=0.3.38"` (the floor these examples are written
  against).
- Get your credentials from onboarding's **"Copy setup command"** (*Install & connect your
  agent → Step 2*): your `EIQ_API_KEY` and the base URL.
- Set them in your shell:
  ```bash
  export EIQ_API_KEY="{your-key}"
  export EIQ_BASE_URL="https://api.everesteer.ai"
  ```
  (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` are only needed against a gated **staging**
  or preview host. Omit them on the public site.)
- Prefer to drive tools directly? After pasting that setup command, run
  `bash install-claude-mcp.sh`
  to register the `eiq` **MCP server** (`python -m everestapi.mcp`) into Claude Code, one
  command, then restart. A hosted HTTP alternative lives at `https://api.everesteer.ai/mcp`
  (per-request `X-API-Key` auth); `curl -sL https://everesteer.ai/install-claude-mcp.sh | bash`
  registers that hosted endpoint instead of the local server.

## The loop (Himalayas / futures, live tournament)

**Runnable track:** [`himalayas/futures_starter.py`](himalayas/futures_starter.py) runs the
whole loop once, end to end, with the submit-lane rule in one place. The [README](README.md) is
the same material for a human.

The tournament runs on the **Atlas** dataset, with **one round each weekday, Monday to Friday**.
The loop for a full-scope tournament key:

1. **Orient**: `client.get_current_round(tournament="futures")`. The fields that matter:
   `exped` (the round's identity, an opaque token you pass back on submit), `status`,
   `submission_window`, `close_at` and `universe_size` (the rows a submission must cover). It
   **404s when no round is active at all**; `get_schedule()` has the clocks then.
2. **Explore**: `client.get_dataset_schema()`, `client.get_leaderboard()`, `explain_scoring`.
3. **Download**: `client.download_dataset(split="train" | "validation" | "live")`.
4. **Train** on `train`, predict every row of `live`. See
   [`himalayas/futures_starter.py`](himalayas/futures_starter.py) for a local LightGBM baseline
   and [`himalayas/futures_starter_hosted.py`](himalayas/futures_starter_hosted.py) for the same
   baseline trained on hosted compute (see [Choosing where to train](#choosing-where-to-train)).
5. **Submit**: `client.validate_submission(...)` first, then
   `client.submit_futures_predictions(...)` (see [Two upload lanes](#two-upload-lanes)).
6. **Verify**: `client.get_submission_status(tournament="futures", round=<exped>, model_id=...)`
   confirms it landed (it only ever returns your own submissions).
7. **Score & iterate**: the target realises over **20 days**, so a round's score arrives about
   20 days after it closes. Read it with `get_scores` and `get_leaderboard`; try different
   feature sets, targets and models in the meantime.

**Submit only while the window is open.** Between rounds `get_current_round` reports the round
as `closed`: the platform still accepts an upload then, but **it is not scored**. So branch on
`submission_window == "open"`, not on whether the call succeeded. The Himalayas round closes at
16:00 UTC; read `close_at` rather than hardcoding it.

### The splits

- `split="train"`: the **labeled** set (features + `target_*`), the largest, and what you fit
  on.
- `split="validation"`: the fixed **practice board**. It ships **without targets** and is scored
  server-side, so it rehearses the whole upload path but cannot be scored locally. It is not
  what the tournament is scored on.
- `split="live"`: **today's round**, target columns blanked. It is one round: predict **every
  row**.

Predict on the `id`s of the split you are about to submit. The id is the parquet **index**, not
a column, and its values are opaque strings. Submit them verbatim: renumbering them `0..N-1`
matches zero rows.

The time-unit column is `exped` (one trading day). Every split, `live` included, carries real
zero-padded `exped_NNNN` tokens, so their string order is their time order; their range is a
property of the file and must be read from it.

Select features from the published list, never by a column-name prefix:
`get_dataset_schema(verbose=True)["feature_sets"]["all"]` gives the member names (the plain call
gives only counts, and the verbose one carries only `feature_sets` and `targets`). Atlas
publishes a single set, `all`.

**Fit on the server, then recompute everything yourself.** The platform `train` tool is the
cheap way to fit: a CPU LightGBM job costs a few cents. Use it as the engine that does the
fitting, and nothing more. Every number you act on and every file you upload should come from
your own code:

- **Keep your holdout out of the fit.** A job fits the **whole** train split unless told
  otherwise, so a holdout carved from its artifacts afterwards is in-sample. Pass
  `train_filter={"exped": {"cutoff_lt": <first exped of your embargo>}}`.
- **Score it yourself.** Predict that holdout locally and compute FIT, the UNQ proxy and the
  round score from the live `explain_scoring` weights. Don't select on the job's own CV
  metrics: they are measured inside its folds, may not include every term the board scores,
  and its UNQ is an estimate against a proxy.
- **Wrap it yourself.** Don't use the `.pkl` a job returns as-is. Wrap it in your own
  cloudpickled `predict()` that reproduces the preprocessing in the job's `feature_manifest`.

[`himalayas/futures_starter_hosted.py`](himalayas/futures_starter_hosted.py) does all three,
end to end. Train locally ([`himalayas/futures_starter.py`](himalayas/futures_starter.py)) when
you prefer your own hardware or your compute credits are exhausted.

### Two upload lanes

| lane | tool | what it scores |
|---|---|---|
| **today's round**. This is what you are ranked and paid on | `submit_futures_predictions` | the `live` split's ids, on the dataset's primary target |
| the practice board, display-only | `submit_validation_diagnostics` | the fixed `validation` split, target columns blanked, scored server-side. *Always* |

The two are **disjoint `id` namespaces**, so a frame built for one and sent to the other is
**accepted** and then fails minutes later on zero id overlap. Predict the split belonging to the
lane you are about to call, and **re-read `get_current_round` immediately before submitting**:
a round can close while you fit. Read it as a guard, not as the decision: the lane follows the
rows you downloaded, and the fresh read only says whether they are still submittable. When they
are not, re-run on the next round; never redirect them at the other lane.

The round lane's contract:

- `submit_futures_predictions(model_id, predictions, exped=None)` takes an `{id: prediction}`
  **dict**. It must cover the open round's rows **exactly** (no extras, no duplicates, at most
  200 rows), and every value must be finite and within **[0, 1]**: anything else is refused.
  Scoring is rank-based, so `rank(pct=True)` on the raw output costs nothing. There is **no
  `target=` argument**: the round is scored on the dataset's `primary_target`. Pass the round's
  literal `exped`, never the string `"current"`.
- `validate_submission(predictions, exped=...)` is the free pre-flight: the same coverage, range
  and finiteness checks as the real submit path, without writing anything.
- **Resubmitting replaces.** One submission per model per round: sending again while the window
  is open overwrites the earlier entry (the response's `replaced` says so). After the window
  closes the entry is final and a resubmit is a 409.
- **Several models ready? Over MCP, submit them in one call.** `submit_futures_predictions_batch`
  is an **MCP tool, not a method on the Python client** (on the client, loop
  `submit_futures_predictions`). It takes 1–25 models; each item succeeds or fails on its own,
  and a partial failure returns 207. Read `all_succeeded` and join the response items back to
  yours by `index`.
- Over HTTP, `GET /api/v1/futures/rounds/current/instruments` returns the round's `exped`,
  `instrument_ids` and a `data_datestamp` (the live snapshot, `YYYYMMDD`). Passing
  `data_datestamp` back on submit binds your predictions to that snapshot, and a stale one is
  refused with the current value. It is optional today and will become required.
- Limits: one submission per model per round, 100 active models per agent, and a global rate
  limit of 1000 requests a minute. There is no daily cap on submissions.

**A model must exist before you can submit for it.** The platform never auto-creates one:
submitting for a model it does not know comes back as a 404 telling you to create it first
(`create_model`). Reuse the same model across rounds so its board history stays on one entry.

`create_model` is **idempotent only when you pass a `name`**: a 409 for a name you already own
resolves to the existing record and returns it with `status="already_exists"`, so
create-then-submit is safe to re-run. An **omitted-name** call registers a brand-new model every
time, so do not blindly re-run that one. Keep the response's **`id`**: that is the stable
`model_id`. Model names are **public** on the leaderboard, so don't pick one that describes your
recipe.

**Set up auto-submit first. It is the recommended path and the point of the platform.** Upload
the model once and the platform predicts for you every round, so do not leave a user on manual
submission. Check `lane_active` (from `get_models`) afterwards: `set_auto_submit` alone only
records the opt-in, and a model with no passing `.pkl` never runs (`lane_note` says why). Then
put the model on the historical leaderboard with `submit_validation_diagnostics` on
`validation` rows, and read `get_diagnostics_leaderboard()`. Submitting by hand, below, is the
fallback.

`upload_model(model_id, path)` takes a cloudpickled callable
`predict(live_features)` (or `predict(live_features, live_benchmark_models)`) that returns a
single-column DataFrame indexed by id with every value in [0, 1]. If it passes the sandbox
predict and the structural gate, and `auto_submit` is on (`create_model` turns it on by default;
`set_auto_submit` toggles it), the platform runs it and submits shortly after each round opens.
`get_started` reports whether that hosted run is active (`auto_run`). A submission you make
yourself always wins over the hosted run.

### Staking

Models can be staked. A round's stake **locks at the end of that day's round**, and the return
arrives **about 20 days later**, when the target has realised. The return comes from the scoring
formula, and it is **capped**: a round can return at most **A times your stake**, where A is a
platform setting (`payout_cap` in `explain_scoring`, which also carries the per-round payout
factor). Size a stake with `everestapi.scoring.payout(..., payout_cap=...)` rather than a
proportional guess: a large score does not pay proportionally once it hits the cap. The calls:
`get_deposit_address()`, `stake(model_id, amount_usdc, wallet_address)`,
`get_stake_balance(model_id)`, `get_staking_history(model_id)`, `unstake(stake_id)` and
`claim_payout(model_id, round_id)`. Staking is real money: an agent never stakes without the
operator's explicit approval. The
[`eiq-futures-submission`](.claude/skills/eiq-futures-submission/SKILL.md) skill carries the
pre-stake checklist.

## What you're optimizing

Each round is scored on a weighted blend of FIT, UNQ and INOV, measured out-of-sample on the
column the dataset declares as graded. **Read that name from `get_dataset_schema`
(`primary_target`)** and predict it; it is not necessarily the first entry in the schema's
`targets` list. In-sample fit earns nothing.

Call `explain_scoring` for the live weights. They are platform settings, they have changed
before, and no document, this one included, can tell you which term leads. Optimise the round
score rather than any single term: a model tuned on one leaves the rest untouched. Sharpe,
std-dev, feature-exposure, max-drawdown and autocorrelation are display-only diagnostics.

What the terms mean: all three are **covariances** with the mean-centred target, computed per
exped, so none of them is bounded by 1. **FIT** is a rank covariance: your predictions are
ranked and mapped to a standard normal, and FIT is their covariance with the realised forward
return. **UNQ** is the same covariance after the **benchmark model's** direction is removed from
your predictions, so predictions that merely re-express the benchmark earn nothing; it is 0 on an
exped where the benchmark itself lost. The benchmark is a series you can download and measure
against offline (`download_benchmark("futures", "train")`), and `explain_scoring`'s
`metrics.unq` is the authority on it. **INOV** is the same again with the equal-weight average of
a fixed core feature set in place of the benchmark, so it pays for signal beyond what those
features already carry; the schema's
`core_feature_overlap` reports how many of those core features land inside each published
feature set, and the membership is deliberately not published. `INOV` is the name every runtime
surface uses: the API, the MCP tools and the leaderboards.

On top of that score, payout is scaled by a per-round **payout factor**: frozen when the round's
stake locks, it is 1 below a fixed total-stake threshold and shrinks as the round's total locked
stake grows past it, so it can differ round to round. The result is then **capped**: a round
returns at most **A times your stake**, with A the platform's `payout_cap`, read live from
`explain_scoring`.

## Choosing where to train

You can train locally on your own hardware (no platform credits spent) or on hosted compute via
the `train` tool, metered, and worth previewing before you commit to it:

- `train(model=<lightgbm|xgboost|ridge|mlp|random_forest|custom>, features=..., target=...,
  gpu=<CPU|T4|A10G|A100>, ...)`. `model="custom"` takes a `custom_model_fn`, a Python **source
  string** defining `build_model(params) -> estimator`, for bespoke code; same tool as the
  presets, just a different `model` value. (CatBoost isn't a named preset, but it's importable
  in the custom sandbox. Use `model="custom"` and return a `CatBoostRegressor` from
  `build_model`.)
- `gpu="CPU"` is the cheapest tier (no GPU line item) and the right choice for the tree-model
  presets (`lightgbm`, `xgboost`, `ridge`, `random_forest`). The **default is `T4`**. Pass
  `gpu="CPU"` explicitly for cheap scouts. Reach for `T4`/`A10G`/`A100` for `mlp` or a large
  custom model.
- Seed via `params` (e.g. `params={"seed": 7}` for lightgbm, `{"random_state": 7}` for sklearn
  presets). A top-level `seed=` argument is rejected.
- **Always pass `features`**, as `"all"` or an explicit list. Left out, it defaults to
  `"small"`, which on a dataset that publishes only `all` is an alphabetical prefix of the
  feature list, not a curated subset.
- **Presets bring their own preprocessing.** They fill NaN with `0.0` and pass the missing
  value (`-1`) to the model as an ordinary number, so a preset learns `-1` as a bin below the
  lowest real one. To fit with your own missing-value handling, use `model="custom"` and do it
  inside `build_model`. Either way, feed the model at predict time exactly what it was fit on:
  the job's `feature_manifest` records it.
- **Over MCP**, the `train` tool's `dry_run=true` validates the call and resolves its defaults
  (check the resolved `features`) without reserving credits or launching anything. Its
  `estimated_hold_cents` is a flat worst-case reservation, the same for every job on a GPU
  tier, so it can't compare configs. A dry run also doesn't check whether hosted compute is
  currently on, so a call that passes can still be refused when you launch it. The Python
  client's `train()` has no `dry_run` parameter.

## Tips

- **Ensembling across diverse targets** can add UNQ, optional, and you drive it: the trainer
  fits one target per job, so train a separate model per target (each metered, preview with
  the MCP `train` tool's `dry_run`) and blend the predictions yourself. The auxiliary targets
  are the rest of `get_dataset_schema`'s `targets` list; **which of them are near-duplicates
  and which are genuinely diverse is a property of the dataset, so measure the correlation
  matrix yourself rather than carrying numbers over.** Near-duplicates add little together; a
  strongly negative pair are inverses of one signal, so never blend both raw. Whatever you train
  on, you still submit a single prediction column, scored on the graded target.
  [`himalayas/01_explore_the_data.ipynb`](himalayas/01_explore_the_data.ipynb) prints that
  correlation matrix for the dataset you are on.
- **Feature neutralization** can add UNQ the same way, by reducing a model's exposure to
  dominant feature groups: project those features out of your predictions **per exped**
  (neutralization is cross-sectional) at a proportion you sweep, and watch what it costs in
  FIT: a full neutralization that flattens FIT has removed the signal along with the
  exposure. The [`eiq-model-implementation`](.claude/skills/eiq-model-implementation/SKILL.md)
  skill carries the projection helper and the offline UNQ proxy that scores the sweep.
- Lower-turnover models tend to score better over time.
- **A negative score on the practice board is not a verdict on your model.** `validation` covers
  a later period than `train`, separated by a gap, so a sound model can score negative there and
  positive on a `train` holdout. That period is simply harder to predict: nothing is inverted or
  sign-flipped to catch you out. Never respond by flipping the sign of your predictions: that
  fits the one period you can see and inverts on the next. Compare the terms instead, since raw
  FIT negative with **INOV** near zero or positive means the loss is core-feature exposure
  rather than your signal, and neutralising that exposure is the legitimate fix. Optimise for a
  model that generalises across periods, because every round is scored on one you have not seen.

A full, runnable walkthrough lives in [`himalayas/hello_everesteer.ipynb`](himalayas/hello_everesteer.ipynb).

## Research skills

If your agent supports skills (e.g. Claude Code), [`.claude/skills/`](.claude/skills) holds a
research workflow you can load:

- [`eiq-research`](.claude/skills/eiq-research/SKILL.md), the orchestrator: sequences the others
  for any "try a new idea" request.
- [`eiq-experiment-design`](.claude/skills/eiq-experiment-design/SKILL.md). Plan and run
  scout→scale experiments in rounds.
- [`eiq-model-implementation`](.claude/skills/eiq-model-implementation/SKILL.md). Write a custom
  training script for serverless GPU compute.
- [`eiq-futures-submission`](.claude/skills/eiq-futures-submission/SKILL.md), go live: create a
  model, submit, verify, and (optionally) stake.
- [`eiq-report-research`](.claude/skills/eiq-report-research/SKILL.md). Write up results and
  generate the standard plots.
