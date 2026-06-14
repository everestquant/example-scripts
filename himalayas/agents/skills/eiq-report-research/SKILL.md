---
name: eiq-report-research
description: Turn a finished EIQ Himalayas (futures) experiment run into a durable, scientific write-up in experiment.md (abstract, motivation, method, results table, decisions, stopping rationale, findings, next steps) and generate/link the standard cumulative-CORR/EAC plot. Use after running EIQ futures experiments, or when asked to "write up the results", "produce a full report", "update experiment.md", or "generate the standard plot".
---

# EIQ Report Research

Convert one or more experiment runs in your `experiments/` folder into a finished report
a reader can follow end to end — what you tested, what won, why you stopped, and whether
you'd stake on it. The goal is a short scientific paper, not a metrics dump.

You own everything here: the `experiments/` folder, the `everestapi` SDK, the EIQ MCP
tools, and the plotting/scoring helpers shipped in this example-scripts repo. There is no
internal platform repo to call into.

## Ground truth (EIQ Himalayas — futures)

- Tournament: **The Himalayas** (futures). Time unit is the **exped**.
- Primary target: `target_everest_20`. Consensus benchmark: `ai_model`.
- Payout: **`0.75 * CORR + 2.25 * AIMC`**, clipped to ±25% of your stake. Uniqueness
  (AIMC) carries roughly 3× the weight of raw accuracy (CORR) — say so in the write-up.
- Always report these:
  - **EAC** — Everest Alpha Contribution: your unique signal beyond the static benchmark
    model (`eiq_minera_model`); same contribution formula as AIMC but vs the static
    benchmark. It is the primary *experiment-selection* metric and the offline proxy for
    AIMC. Report it **two ways**: full-period EAC and a recent-window EAC (most recent
    ~20–40 expeds).
  - **AIMC** — AI Model Contribution: contribution beyond the live stake-weighted
    ai-model consensus; the 2.25-weighted payout driver and the live target EAC proxies
    for. Report it where rounds have resolved.
  - **CORR** — mean per-exped rank correlation of your predictions vs the target; the
    0.75-weighted payout component.
  - **correlation-with-benchmark** — corr of your preds with `ai_model`. This is the
    tell for the "high CORR, low EAC/AIMC" trap: a model that just re-derives the consensus.
  - **stability** — per-exped sharpe (mean/std of the per-exped score) and max drawdown
    of the cumulative score.

## Step 1 — Inventory what actually ran

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
- A config with no artifacts is *planned only* — it goes in the report as "configured,
  not run", never in the results table as if it had numbers.

If the run was staged in rounds, capture each round's **intent** (what changed vs the
prior round) and whether it beat the running best.

## Step 2 — Pull the numbers

Compute metrics from the out-of-sample predictions, or pull them with the SDK / MCP for
anything already submitted:
- `get_scores` — per-exped CORR/AIMC history for a submitted model.
- `get_round_diagnostics` — round-level summary.
- `run_validation_diagnostics` — validation-split metrics for a candidate.
- `get_model_per_exped_breakdown` — per-exped series for stability stats and the plot.
- `get_leaderboard` — context vs the field.

Pick the **best model by EAC** (recent-window EAC breaks ties) — EAC is the
experiment-selection metric and the offline proxy for AIMC, the live payout driver. Use
CORR as the secondary accuracy check and per-exped stability (plus resolved-round AIMC
where available) to confirm the edge isn't a single lucky exped. A high-CORR model with
near-zero EAC and high benchmark correlation is *not* the winner — flag it.

## Step 3 — Write experiment.md

Use this template. Keep prose tight; every section earns its place.

```markdown
# <Experiment Name> — Experiment Report

**Date:** YYYY-MM-DD
**Tournament:** Himalayas (futures)
**Target:** target_everest_20
**Selection metric:** EAC (vs static benchmark; offline proxy for AIMC)  ·  **Payout:** 0.75·CORR + 2.25·AIMC (±25% cap)

## Abstract
Two to four sentences: what was tested, the headline result, and the decision
(stake / not yet). Lead with the EAC outcome (the AIMC proxy; AIMC alongside where resolved), not CORR.

## Motivation
Why this idea should produce alpha *beyond the consensus* — i.e. why it should move
EAC (and so AIMC, the payout driver it proxies for), not just CORR. State the hypothesis
you set out to test.

## Method
- Data: train / validation / live exped ranges actually used.
- Feature set and any transforms.
- Model type(s) and key hyperparameters.
- Cross-validation: scheme + embargo (note 20-day targets need a wider exped embargo).
- How each round differed from the previous (if staged).

## Experiments run
One short subsection per config that *actually ran*. Name the artifacts
(results/preds files). List planned-but-not-run configs separately and clearly.

## Results

| Model | Round | EAC (full) | EAC (recent) | CORR | AIMC (resolved) | corr_w/_benchmark | per-exped sharpe | max DD | payout (est) | Status |
|-------|-------|-------------|---------------|------|-----------|-------------------|------------------|--------|--------------|--------|
| ...   | ...   | ...         | ...           | ...  | ...       | ...               | ...              | ...    | ...          | best / kept / dropped |

`payout (est) = 0.75·CORR + 2.25·AIMC` (before the ±25% stake cap). Call out any
high-CORR / low-EAC rows explicitly — they look good and aren't.

### Round-by-round
For each round: what changed, the best result, and whether it beat the prior best.

### Per-cluster breakdown
Does the edge generalize across the futures clusters (eq, rates, fossil_energy,
agriculture, fx, eu_power, metals, volatility), or is it concentrated in one or two?
A cluster-concentrated edge is fragile — say so.

## Standard plot
![cumulative CORR and EAC vs ai_model](plots/cumulative_corr_eac.png)
Cumulative CORR and cumulative EAC of the best model vs the `ai_model` benchmark over
expeds. Interpret it: is EAC accumulating steadily, flat, or decaying recently?

## Decisions
The choices you made and why (feature set, model family, sweep picks, per-exped vs
global). Frame them against EAC (the offline proxy for AIMC), not CORR.

## Stopping rationale
Why you stopped iterating — e.g. EAC plateau over N rounds, recent-window EAC decaying,
diminishing payout per round, or a confirmatory full-data run after a scout phase.

## Findings
What worked, what didn't, what the plot and per-cluster view actually show. Honest
about negative results.

## What we'd stake / why (or not yet)
A clear call in payout terms: would you stake this model, and why — or what specifically
must improve first (e.g. recent EAC, cluster breadth, benchmark de-correlation).

## Next experiments
2–5 concrete, prioritized follow-ups tied to the findings above.
```

## Step 4 — Generate the standard plot

The standard EIQ plot is **cumulative CORR and cumulative EAC of the best model vs the
`ai_model` benchmark, over expeds**, built from the run's out-of-sample predictions.

If the example-scripts repo ships a plotting helper, use it, e.g.:

```bash
python plot_experiment.py \
  --predictions experiments/<name>/predictions/<best_model>.parquet \
  --benchmark ai_model \
  --out experiments/<name>/plots/cumulative_corr_eac.png
```

Otherwise (the fallback always works), a minimal matplotlib equivalent (compute the
per-exped CORR and per-exped EAC series, cumsum each, plot both vs the benchmark line):

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# expeds: ordered list; corr_cum, eac_cum, bench_cum: cumulative per-exped series
NAVY, TEAL, CORAL = "#09142F", "#007B63", "#EC9A5F"
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(expeds, corr_cum, color=TEAL, label="Cumulative CORR")
ax.plot(expeds, eac_cum, color=CORAL, label="Cumulative EAC (vs ai_model)")
ax.plot(expeds, bench_cum, color=NAVY, linestyle="--", label="ai_model benchmark")
ax.axhline(0, color=NAVY, linewidth=0.5)
ax.set_xlabel("exped"); ax.set_ylabel("cumulative score")
ax.set_title("Best model vs ai_model over expeds")
ax.legend()
fig.tight_layout()
fig.savefig("experiments/<name>/plots/cumulative_corr_eac.png", dpi=150)
```

Embed it with a **relative** link so it resolves from inside the experiment folder. If
you have several strong candidates, either overlay them on one plot or emit one per
candidate and link each.

## Step 5 — Final checks

- The plot file exists under `plots/` and the relative link in `experiment.md` resolves.
- Every number in the results table traces back to a real `results/` artifact.
- Runs that only have a config (no artifacts) are labeled planned, never tabulated as
  results.
- EAC is reported both full-period and recent-window (AIMC alongside where rounds have
  resolved); benchmark correlation is shown so high-CORR/low-EAC cases are visible.
- The per-cluster breakdown is present and interpreted.
- The payout framing uses **0.75·CORR + 2.25·AIMC** (±25% cap) — never 0.50/2.50.
- The "what we'd stake / why (or not yet)" conclusion is explicit.
- No synthetic data: all metrics come from real EIQ predictions and scores.
