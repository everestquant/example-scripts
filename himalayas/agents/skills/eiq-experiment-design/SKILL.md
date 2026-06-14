---
name: eiq-experiment-design
description: >-
  Plan, run, and iterate EIQ Himalayas (futures) experiments using the scout→scale
  research loop. Clarify the idea, align a baseline against ai_model, write configs,
  train via the EIQ MCP server (quick_train / custom_train), optimize EAC (the offline
  validation-time proxy for the live AIMC payout metric) as the primary
  experiment-selection metric, iterate in rounds, stop at a plateau, and scale the
  winner. Use
  when asked to design a futures experiment, decide what to try next, or turn a model
  idea into a structured, multi-round research plan.
---

# EIQ Experiment Design (Himalayas / Futures)

A repeatable research loop for the **Himalayas** futures tournament on the EIQ platform.
You hold the `everestapi` SDK, the EIQ MCP server, your downloaded datasets, and the
example-scripts helpers — that is all you need. Everything below is framed around those
tools and a `configs/` + `experiments/` layout that **you own** in your own repo.

The job is never "get one good run." It is: turn an idea into a sequence of cheap,
interpretable rounds, read the evidence, and decide the next round — until the signal
stops improving.

## Ground truth you must not get wrong

- **Universe** = futures **chains** grouped into **clusters** (energy, rates, ags, FX,
  metals, equity, power, vol, …). Discover the live shape with `get_universe` /
  `get_features` — never hardcode counts.
- **Time unit** = **exped** (plural *expeds*). CV is **exped-purged + embargoed**.
- **Primary target**: `target_everest_20` (20-day forward, rank-normalized).
- **Features** are obfuscated, quintile-binned into `{0,1,2,3,4}`, named
  `feature_<theme>_<n>`. They are already binned — do not re-standardize them.
- **Benchmark** = `ai_model` (pull it with `download_benchmark`). Your baseline and every
  comparison aligns to it.
- **Metrics**:
  - **CORR** — per-exped rank correlation of your predictions vs `target_everest_20`; the
    0.75-weighted payout component.
  - **EAC** — Everest Alpha Contribution: your unique signal *beyond the static benchmark*
    model (`eiq_minera_model`). Same contribution formula as AIMC, but measured against the
    static benchmark instead of the live ai-model. During research you don't have the live
    meta-model, so **EAC is the metric you select experiments on** — the offline
    validation-time proxy for AIMC.
  - **AIMC** — AI Model Contribution: the same contribution formula measured against the
    *live* stake-weighted ai-model consensus. It carries the 2.25 payout weight and is the
    ultimate target, but is only measurable once rounds resolve — which is why you optimize
    EAC offline to chase it.
  - Always sanity-check **correlation-with-benchmark**: a config with high CORR but EAC
    near zero is just re-expressing the static benchmark / `ai_model` and earns nothing extra.
- **Payout = 0.75·CORR + 2.25·AIMC**, capped at ±25% of stake. AIMC is weighted 3× CORR —
  uniqueness pays far more than raw accuracy. Design for divergence from the crowd, and
  use EAC as the offline proxy you actually optimize toward it.

## The loop in one breath

```
clarify idea → align baseline to ai_model → scout round (cheap, sampled)
→ read EAC + benchmark-corr (CORR alongside) → decide next round → repeat → plateau? → scale winner → confirm
```

---

## Step 0 — Clarify the idea (disambiguate before spending compute)

If the request is vague ("try a momentum angle", "make it more robust"), do **not** guess.
Enumerate **2–4 genuinely different interpretations**, run one cheap scout `quick_train`
per interpretation on a sampled subset, and let EAC pick the winner.

> *"Add cross-asset features"* could mean: (a) include FX + rates features in an
> energy-focused model, (b) train one model across all clusters jointly, or (c) build
> per-cluster models and blend. These are different experiments. Scout all three at
> `sample_pct≈0.25`, compare EAC, commit to the best, and write down why in `experiment.md`.

Document the chosen interpretation and the rejected ones — that reasoning is part of the result.

## Step 1 — Planning checklist (answer before any training)

- **Idea & novelty.** One sentence: what is being tested and why it might add EAC/AIMC.
- **Research type.** New target/feature-eng · new architecture · ensemble/blend ·
  training-procedure · data/universe change. This decides what you sweep (see below).
- **Baseline.** Always `ai_model`. Download it and score it on validation so every round
  has a baseline row.
- **Primary metric** = **EAC** (selection — the offline proxy for AIMC, the live payout
  driver). **Secondary** = CORR (the 0.75 payout component, above noise). **Diagnostics** =
  benchmark-corr (not too high) and per-exped stability.
- **Budget.** Max rounds (≈4–5 expected), compute credits, wall-clock. Check
  `get_compute_credits` before you start so you don't strand a round half-finished.
- **Stopping rule.** Pre-commit to the plateau criterion below *now*, before you see results
  — this prevents fishing for a lucky round.

## Step 2 — Folder layout (you own this)

One experiment = one line of inquiry = one folder. Keep configs, results, and your
narrative together so the whole study is reproducible from the repo alone.

```
experiments/<experiment_name>/
  experiment.md            # hypothesis, baseline, per-round tables, decisions, final story
  configs/
    r1_lgbm_small.yaml     # one file per config; name = the single thing it varies
    r1_xgb_small.yaml
    r2_lgbm_all.yaml
  results/
    r1.csv                 # validation metrics for every config in round 1
    r2.csv
  predictions/             # saved val/live prediction parquets per promoted config
  best.pkl                 # winning model artifact (downloaded from its compute job)
```

`experiment.md` is the lab notebook. It opens with the hypothesis and the declared baseline,
gains a metrics table after each round, and ends with a short narrative of what worked, what
didn't, and which config won.

## Step 3 — Rounds, not runs (persistence is required)

Work in **rounds of ~4–5 configs**. Within a round, change **exactly one variable per config**
so the comparison is causal. A round is only finished when **every** job in it has completed —
poll `get_job_status`, then synthesize. Do not report off a single early-returning run.

After each round:
1. Pull validation diagnostics for each config (`run_validation_diagnostics`).
2. Build the round table in `experiment.md`: EAC, CORR, benchmark-corr, plus a
   per-cluster EAC breakdown and a stability number (AIMC too once rounds resolve).
3. Pick the round winner by **EAC** (CORR as tie-breaker; stability as a
   diagnostic).
4. Decide the next round: which dimension to push, what to drop. Write the decision down.

## Step 4 — Scout → Scale

**Scout (early rounds).** Iterate fast and cheap so most ideas die before they cost much.
- Sample the expeds: a **scout subset** = every Nth exped (e.g. `sample_pct≈0.25`, ~25%).
- Use a small feature subset and modest model sizes.
- Run via `quick_train` (templated configs) — this is the cheap tier.
- Evaluate on the **full** validation split even though you trained on a sample, so the
  metric isn't itself sampling-noisy.

**Scale (later rounds).** Promote only the top 1–2 scout configs (by EAC).
- Move to full expeds (`sample_pct≈1.0`) and richer features.
- Use `custom_train` (GPU, your own script) when a winner needs a bigger model or a
  custom objective the templates don't cover.
- Expect the metric to move when you scale — that's the point. A scout result that
  *collapses* at full scale was overfit to the sample; keep the version that survives.

**One confirmatory scale step.** After you plateau on sampled data, run the surviving config
once at full expeds + full features to confirm the edge is real before reporting/submitting.

## Step 5 — Plateau / stopping criteria

Stop when **two consecutive rounds fail to beat the running-best EAC by a meaningful margin**
*and* the untried knobs are either redundant with what you already swept or likely to just
raise benchmark-correlation (which would erode EAC/AIMC). Then do the single confirmatory scale
step and write the report.

What "meaningful" means is yours to set per study — fix the threshold up front and judge it
against round-to-round EAC noise, not against zero. A 0.0001 wobble inside the noise band is a
plateau, not progress. Record the explicit decision in `experiment.md`:

```
Round 1 → 2:  EAC +0.0021   continue
Round 2 → 3:  EAC +0.0006   continue
Round 3 → 4:  EAC +0.0001   within noise
Round 4 → 5:  EAC +0.0000   within noise  → STOP (2 flat rounds)
Winner: r3_lgbm_all  (EAC best, benchmark-corr 0.71, holds across all clusters)
```

---

## Sweep selection by research type

Match the sweep to the question. One variable at a time, per config, within a round.

| Research type | What to vary | What to leave fixed |
|---|---|---|
| **New target / feature engineering** | target variant or transform, feature subset, binning/preprocessing | model + hyperparameters (use a fixed reference model) |
| **New architecture** | depth/width, learning rate, regularization, estimators/epochs | features, target |
| **Ensemble / blend** | member weights, blend rule, bag count, stacker | the members themselves |
| **Training procedure** | residualization strength vs `ai_model`, loss weighting, cluster sample-weights, embargo | model + features |
| **Data / universe change** | cluster inclusion, exped sampling, feature-set size | model + target |

If one parameter clearly dominates the results, spend a whole round mapping its range
(including the extremes) with everything else pinned.

---

## Futures-specific evaluation (don't skip this)

Himalayas is futures, and a single average metric hides the things that sink futures models.

- **Cluster-aware EAC.** Break EAC down per cluster (and AIMC per cluster once rounds
  resolve). An edge that lives entirely in one cluster (e.g. energy) is fragile and
  may be a roll/liquidity artifact, not skill. Favor configs whose EAC is positive across
  *several* clusters.
- **Per-exped stability & drawdown.** Look at the spread of per-exped CORR/AIMC and the
  worst run of negative expeds, not just the mean. A high-mean, high-variance config that
  spends quarters underwater is worse than a steadier one at the cap.
- **Contract-roll / liquidity awareness.** Continuous-futures series carry roll seams, and
  thin contracts add noise. Distrust an edge concentrated around roll windows or in the
  least-liquid chains — verify it survives when those expeds/instruments are down-weighted.
- **Cluster sample-weighting.** Clusters differ in size and signal density. Consider
  weighting so a few large clusters don't quietly dominate training; treat the weighting
  itself as a sweepable training-procedure dimension.
- **EAC/AIMC > raw CORR, always.** Because payout weights AIMC 3×, prefer designs that
  *diverge* from the static benchmark, `ai_model`, and the crowd. When two configs tie on
  CORR, take the one with lower benchmark-corr and higher EAC — that's the one that, once
  live, actually pays (EAC is your offline read on it).

---

## Baseline alignment to `ai_model`

- Declare `ai_model` as the baseline in `experiment.md` and include a baseline row in every
  results table.
- Pull it once per universe/split with `download_benchmark` and score it the same way you
  score your configs, so the EAC comparison (and AIMC once rounds resolve) is apples-to-apples.
- Keep the feature set consistent between a config and the baseline comparison you cite — a
  richer-feature config beating a small-feature baseline tells you nothing.

---

## Tooling map (MCP + SDK)

| Need | Tool |
|---|---|
| See the universe / clusters / features | `get_universe`, `get_features` |
| Download data + understand columns | `download_dataset`, `get_dataset_schema` |
| Download the `ai_model` benchmark | `download_benchmark` |
| Cheap templated training (scout) | `quick_train` |
| Custom / GPU training (scale) | `custom_train` |
| Poll a training job | `get_job_status` |
| Check budget before a round | `get_compute_credits` |
| Validation metrics for a config | `run_validation_diagnostics` |
| Round-level diagnostics | `get_round_diagnostics` |
| Submit the winner | `submit_futures_predictions` |

Confirmed signatures: `quick_train(model=<lightgbm|xgboost|ridge|mlp>,
features=<small|medium|all>, target="target_everest_20", universe="futures")` (target and
universe default as shown; cost ~$0.005–0.05, returns a job — poll `get_job_status`).
`custom_train(script_path="train.py", gpu=<T4|A40|A100>, max_hours=2.0,
requirements=[...])` (cost ~$0.50–1.79/hr; pod has the SDK + PyTorch preinstalled).

A typical scout round, conceptually:

```text
get_compute_credits                      # enough budget for ~4 configs?
get_universe / get_features              # confirm clusters + feature names this round
download_dataset(universe="futures", split="train")
download_benchmark(universe="futures", split="validation")   # ai_model baseline
for cfg in round_1_configs:              # ~4 configs, each varies ONE thing, sampled expeds
    quick_train(...)  -> job_id
poll get_job_status(job_id) until all done
run_validation_diagnostics(...) per config
# write results/r1.csv + experiment.md table; pick best EAC; decide round 2
```

## Reporting

When you stop, write the closing section of `experiment.md` as a short scientific narrative,
not a metrics dump: the hypothesis, the path the rounds took, what won and *why*, the final
table (with the `ai_model` baseline row and per-cluster EAC, AIMC where resolved), and the explicit stopping
decision. Then submit the confirmed winner with `submit_futures_predictions` if deployment is
the goal — so a single session can carry an idea from clarification all the way to a live model.
