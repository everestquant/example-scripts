---
name: eiq-report-research
description: Turn a finished Everesteer daily futures tournament experiment run into a durable, scientific write-up in experiment.md (abstract, motivation, method, results table, decisions, stopping rationale, findings, next steps) and generate/link the standard cumulative-FIT plot. Use after running Everesteer futures experiments, or when asked to "write up the results", "produce a full report", "update experiment.md", or "generate the standard plot".
---

# Everesteer Report Research

Convert one or more experiment runs in your `experiments/` folder into a finished report
a reader can follow end to end: what you tested, what won, why you stopped, and whether
you'd stake on it. The goal is a short scientific paper, not a metrics dump.

You own everything here: the `experiments/` folder, the `everestapi` SDK, the Everesteer MCP
tools, and the plotting/scoring helpers shipped in this example-scripts repo. There is no
internal platform repo to call into.

## Ground truth (Everesteer daily tournament, futures)

- One round each weekday (Monday to Friday). Time unit is the **exped**.
- The panel is obfuscated: rows carry no instrument identity and there is **no cluster or
  sector column**, so time is the only axis a breakdown can use. Take feature names from
  `get_dataset_schema(verbose=True)["feature_sets"]["all"]`; the other columns are `exped`,
  `data_type` and `target_*`.
- Primary target: the column `get_dataset_schema` reports as `primary_target`. Benchmark:
  the benchmark model's predictions from `download_benchmark("futures", "train")`, the
  series UNQ is measured against, named by the column you find in that frame rather than
  assumed.
- Payout: a weighted blend of FIT, UNQ and INOV. Call `explain_scoring` for the live
  weights; don't hardcode which term dominates, it has changed before. That score is then
  scaled by a per-round **payout factor**, frozen when stakes lock at the end of the daily
  round: 1 below a fixed total-stake threshold, shrinking above it, so it can differ round
  to round, and the return is capped at **A times the stake** (A is the platform's
  `payout_cap`). Stake returns arrive after 20 days, when the target is realised.
- Always report these:
  - **FIT**: mean per-exped rank covariance of your predictions with the target (ranked,
    mapped to a standard normal, then the covariance with the mean-centred target, so it is
    not bounded by 1); also a scored term (see `explain_scoring` for the live weights), and
    the one number you can compute most precisely offline; one input to the selection score
    (Step 2), not the whole of it. Report it **two ways**: full-period FIT and a
    recent-window FIT (most recent ~20-40 expeds).
  - **UNQ**: the same covariance after the benchmark model's direction is removed from your
    predictions; a paid component. Report the offline `contribution()` proxy from
    **`eiq-model-implementation`** (it residualizes against the downloaded benchmark, the
    same series the server uses) for every config, labeled as a proxy, and the server's
    number where rounds have resolved.
  - **INOV**: UNQ's calculation with the equal-weight average of a frozen core feature set,
    whose membership is not published, in place of the benchmark. Report it where rounds
    have resolved; you cannot reproduce it exactly offline without the core set. Report a
    resolved INOV of `null` as null, never as zero: it means the
    core features were absent from the scored frame, which leaves that round without a
    round score.
  - **correlation-with-benchmark**: corr of your preds with the benchmark series. This is
    the tell for the "high FIT, high correlation-with-benchmark" trap: a model that just
    re-derives the benchmark and is unlikely to earn UNQ once resolved.
  - **stability**: per-exped sharpe (mean/std of the per-exped score) and max drawdown
    of the cumulative score. The right selection diagnostic offline.

## Step 1: Inventory what actually ran

Find the experiment folder. A typical layout:

```
experiments/<experiment_name>/
  experiment.yaml        # hypothesis + config
  configs/               # per-model configs you defined
  results/               # metric outputs (one file per run that executed)
  predictions/           # out-of-sample preds (one file per run that executed)
  experiment.md          # the report you write here
  plots/                 # standard plot output
```

Separate **what ran** from **what is only configured**:
- A config that has a matching `results/` + `predictions/` artifact ran.
- A config with no artifacts is *planned only*. It goes in the report as "configured,
  not run", never in the results table as if it had numbers.

If the run was staged in rounds, capture each round's **intent** (what changed vs the
prior round) and whether it beat the running best.

## Step 2: Pull the numbers

Compute metrics from the out-of-sample predictions you already hold locally, or pull them
with the SDK / MCP for anything already submitted:
- `get_scores`: per-exped FIT/UNQ history for a submitted model.
- `get_round_diagnostics` (SDK): round-level summary. Over MCP, `get_round_daily_progression`.
- `run_validation_diagnostics`: validation-split metrics for a candidate (MCP name for
  `get_validation_diagnostics`).
- `get_model_per_exped_breakdown` (SDK): per-exped series for stability stats and the plot.
  Over MCP, `get_model_daily_progression`.
- `get_leaderboard`: context vs the field.

Build the per-exped stability series (sharpe, drawdown) yourself from the out-of-sample
predictions on disk. This skill's numbers should trace back to files in your own
`experiments/` folder wherever possible.

Pick the **best model by the offline round score**: the live `explain_scoring` weights
applied to holdout FIT and the `contribution()` UNQ proxy (recent-window FIT breaks
ties). Don't pick on FIT alone. It is the term you can compute most precisely offline,
but the board ranks on the blend. Use correlation-with-benchmark as the differentiation
check and per-exped stability (plus resolved-round UNQ where available) to confirm the
edge isn't a single lucky exped. A high-FIT model with high correlation-with-benchmark
is *not* clearly the winner, flag it as a likely benchmark-echo and note that its UNQ,
once a round resolves, may disappoint.

## Step 3: Write experiment.md

Use this template. Keep prose tight; every section earns its place.

```markdown
# <Experiment Name>: Experiment Report

**Date:** YYYY-MM-DD
**Tournament:** futures (daily)
**Target:** <the schema's primary_target>
**Selection metric:** offline round score (`explain_scoring` weights on FIT + UNQ proxy), with correlation-with-benchmark as the differentiation guard  ·  **Payout:** weighted FIT+UNQ+INOV blend (see `explain_scoring` for live weights)

## Abstract
Two to four sentences: what was tested, the headline result, and the decision
(stake / not yet). Lead with FIT and correlation-with-benchmark (UNQ alongside where resolved).

## Motivation
Why this idea should produce alpha *beyond the benchmark*. I.e. why it should lower
correlation-with-benchmark (and so raise UNQ), not just raise FIT. State the hypothesis
you set out to test.

## Method
- Data: train / validation / live exped ranges actually used.
- Feature set and any transforms.
- Model type(s) and key hyperparameters.
- Cross-validation: scheme + embargo. The target realises over 20 days, so state the
  embargo you used (20 expeds by default) and say if you widened it.
- How each round differed from the previous (if staged).

## Experiments run
One short subsection per config that *actually ran*. Name the artifacts
(results/preds files). List planned-but-not-run configs separately and clearly.

## Results

| Model | Round | FIT (full) | FIT (recent) | corr_w/_benchmark | UNQ (resolved) | per-exped sharpe | max DD | payout (est) | Status |
|-------|-------|-------------|----------------|-------------------|------------------|------------------|--------|--------------|--------|
| ...   | ...   | ...         | ...            | ...               | ...              | ...              | ...    | ...          | best / kept / dropped |

`payout (est)` is the weighted FIT+UNQ+INOV blend, before the payout factor.
`explain_scoring` reads the weights live, so don't hardcode an ordering. Call out any
high-FIT / high-corr_w/_benchmark rows explicitly. Accuracy that differentiates nothing
scores well offline and still pays badly on UNQ once the round resolves.

### Round-by-round
For each round: what changed, the best result, and whether it beat the prior best. Keep
your experiment rounds and the tournament's weekday scoring rounds clearly distinct.

### Robustness over time
There is no cluster or sector axis on this panel, so the fragility check is temporal: split
the holdout in half and report whether the edge survives in both. An edge confined to one
stretch of expeds is a regime artifact, say so. Per-exped FIT spread and the worst run of
negative expeds belong here too.

## Standard plot
![cumulative FIT and correlation-with-benchmark](plots/cumulative_fit.png)
Cumulative FIT of the best model, and its rolling correlation with the benchmark, over
expeds. Interpret it: is FIT accumulating steadily, and is correlation-with-benchmark
trending down (more differentiated) or up (converging on the benchmark)?

## Decisions
The choices you made and why (feature set, model family, sweep picks, per-exped vs
global). Frame them against correlation-with-benchmark (the differentiation guard), not
just FIT.

## Stopping rationale
Why you stopped iterating, e.g. FIT plateau over N rounds, recent-window
correlation-with-benchmark no longer improving, diminishing payout per round, or a
confirmatory full-data run after a scout phase.

## Findings
What worked, what didn't, what the plot and the over-time robustness view actually show.
Honest about negative results.

## What we'd stake / why (or not yet)
A clear call in payout terms: would you stake this model, and why, or what specifically
must improve first (e.g. temporal breadth, benchmark de-correlation, resolved-round UNQ
once available).

## Next experiments
2-5 concrete, prioritized follow-ups tied to the findings above.
```

## Step 4: Generate the standard plot

The standard Everesteer plot is **cumulative FIT of the best model, plus its rolling
correlation with the benchmark, over expeds**, built from the run's out-of-sample
predictions.

This repo ships no plotting helper, so build it yourself: compute the per-exped FIT
series and cumsum it, compute the rolling correlation-with-benchmark series, and plot both
against the benchmark line. A minimal matplotlib version:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# expeds: ordered list; fit_cum: cumulative per-exped FIT;
# bench_corr_roll: rolling correlation-with-benchmark; bench_cum: cumulative benchmark score
# the benchmark is download_benchmark("futures", "train"), the series UNQ is measured against
NAVY, TEAL, CORAL = "#09142F", "#007B63", "#EC9A5F"
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(expeds, fit_cum, color=TEAL, label="Cumulative FIT")
ax.plot(expeds, bench_corr_roll, color=CORAL, label="Rolling correlation-with-benchmark")
ax.plot(expeds, bench_cum, color=NAVY, linestyle="--", label="benchmark")
ax.axhline(0, color=NAVY, linewidth=0.5)
ax.set_xlabel("exped"); ax.set_ylabel("cumulative score / correlation")
ax.set_title("Best model vs the benchmark over expeds")
ax.legend()
fig.tight_layout()
fig.savefig("experiments/<name>/plots/cumulative_fit.png", dpi=150)
```

Embed it with a **relative** link so it resolves from inside the experiment folder. If
you have several strong candidates, either overlay them on one plot or emit one per
candidate and link each.

## Step 5: Final checks

- The plot file exists under `plots/` and the relative link in `experiment.md` resolves.
- Every number in the results table traces back to a real `results/` artifact.
- Runs that only have a config (no artifacts) are labeled planned, never tabulated as
  results.
- FIT is reported both full-period and recent-window (UNQ alongside where rounds have
  resolved); correlation-with-benchmark is shown so high-FIT/benchmark-echo cases are
  visible.
- The over-time robustness split is present and interpreted (there is no cluster axis on
  this panel to break down instead).
- The payout framing uses the weighted FIT+UNQ+INOV blend, per `explain_scoring` (no
  hardcoded ordering or cap number).
- The "what we'd stake / why (or not yet)" conclusion is explicit.
- No synthetic data: all metrics come from real Everesteer predictions and scores.
