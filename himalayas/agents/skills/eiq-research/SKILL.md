---
name: eiq-research
description: >
  Top-level orchestrator for Everesteer Himalayas (futures) tournament research. Drives a
  new idea from hypothesis to a submitting model by sequencing four sibling skills:
  eiq-experiment-design, eiq-model-implementation, eiq-futures-submission, and
  eiq-report-research. Reach for this whenever the request is "try/test a new idea",
  "run an experiment", "sweep configs", "compare models", "improve my Everesteer scores",
  or any open-ended "do Everesteer futures research" task. It enforces scout-then-scale
  discipline, benchmarks against the Everesteer ai_model, optimizes EAC (the offline
  validation-time proxy for AIMC) as the experiment-selection metric, and treats
  per-exped stability as a diagnostic.
---

# Everesteer Research Orchestrator

You are running research on **Himalayas**, Everesteer's live agent-native *futures* tournament.
(Alps, the equities tournament, is pre-launch — ignore it unless the user names it.)
This skill does not do the work itself; it routes you through the four sibling skills
in the right order and holds the connective tissue — defaults, gates, and handoffs —
so a loose "try this idea" request lands as a submitted, documented model.

You only have the **participant surface**: the `everestapi` SDK (`pip install everestapi`),
the Everesteer MCP server (`python -m everestapi.mcp`, tools prefixed `mcp__eiq__*`), datasets
you download, and the helpers shipped in the public `example-scripts` repo. There is no
internal platform source to read.

## The shape of every research run

```
  orient  →  design  →  (implement?)  →  scout  →  scale  →  report  →  submit
            \_______ eiq-experiment-design ______/         \_ report _/ \ submit /
                       \_ eiq-model-implementation (only if new code) _/
```

Skip nothing silently. If you skip a stage, say why.

---

## Stage 0 — Orient (do this yourself, fast)

Pull the current state before committing compute. A few MCP calls:

- `get_current_round` / `get_schedule` — where are we in the round cadence, when is the next close?
- `get_universe` + `get_features` — which chains/clusters and which `feature_<theme>_<n>`
  columns exist this round. Features are obfuscated, integer quintile-binned {0,1,2,3,4};
  do not try to attach economic meaning.
- `get_dataset_schema` — confirm the target column (`target_everest_20`) and split layout.
- `get_models` + `get_scores` + `get_leaderboard` — what you already have running and how it ranks.
- `get_compute_credits` — your budget ceiling for the whole run.

Write a two-line read of the situation: what's the bottleneck — raw accuracy (low CORR)
or differentiation (CORR fine, EAC/AIMC flat)? That framing drives the design stage.

---

## Stage 1 — Design  →  hand to `eiq-experiment-design`

Translate the idea into a concrete plan. Invoke **`eiq-experiment-design`**, which is
responsible for:

- pinning the hypothesis and the one metric that decides win/lose (default: **EAC** —
  the offline proxy for AIMC — as the experiment-selection metric, with CORR and
  per-exped stability alongside),
- choosing the feature scope and CV (exped-purged + embargoed — never plain k-fold),
- laying out **rounds of ~4-5 configs**, scout-sized first,
- defining the plateau rule and the scale trigger.

If the idea is vague, let the design skill run a couple of cheap scout probes to
disambiguate rather than guessing. Do not start firing `quick_train` jobs from here —
that is the design skill's job to specify and the run stages' job to execute.

---

## Stage 2 — Implement *only if the idea needs new code*  →  `eiq-model-implementation`

Most ideas ride existing model families (`quick_train` covers templated LightGBM and
friends; `custom_train` runs an arbitrary GPU script). Reach for
**`eiq-model-implementation`** only when the hypothesis genuinely requires a new
estimator, a custom target transform, or bespoke `fit`/`predict` behaviour. That skill
writes the training script, wires it for `custom_train`, and proves it with a smoke run
(a non-degenerate CORR on a tiny sample) before you spend real credits.

If you are reusing what exists, state "no new code needed" and move on.

---

## Stage 3 — Scout (cheap, wide)

Execute round one exactly as the design specified.

- Downsample: run on a **subset of expeds**, not the full history. The point is to rank
  configs cheaply, not to measure final performance.
- Prefer `quick_train` for templated configs; reserve `custom_train` for the variants
  that need it. Poll with `get_job_status`; pull artifacts with `get_model_download_url`.
- Score every config the same way: download the benchmark (`download_benchmark` /
  `get_benchmarks`) and evaluate predictions against the Everesteer **`ai_model`**. Compute
  CORR, **EAC**, and (where rounds have resolved) AIMC, and use
  `run_validation_diagnostics` for a sanity pass.
- Rank by **EAC** — your contribution beyond the static benchmark, the offline
  validation-time proxy for AIMC (the live payout driver). CORR is the secondary accuracy
  component, and per-exped stability is a robustness check. Promote the top ~2 configs.

**Gate before you scale:** if no config clears the design's EAC floor, do not scale.
A config with healthy CORR but flat EAC is shadowing the static benchmark (and, live,
the meta-model) — change the idea (new feature angle, a benchmark-residualized target to
raise EAC/AIMC by lowering correlation with the benchmark and meta-model) rather than
throwing more data at it.

---

## Stage 4 — Scale (only the survivors)

Re-run the promoted configs at full exped coverage and a wider feature scope, plus a
couple of tight variations. Confirm EAC holds up out of sample and that the lift was not
a small-sample artifact. Re-check on the held-out split before declaring a winner.

**Stop** when any of these is true:

- two straight rounds add no meaningful EAC (you have plateaued),
- EAC is clearly positive *and* CORR is real but not suspiciously high (very high CORR
  on training expeds usually means overfit, not skill); per-exped stability
  corroborates,
- remaining compute credits no longer justify another round.

Track everything as you go — one row per config per round (CORR / EAC / AIMC / notes).
A per-model breakdown via `get_model_per_exped_breakdown` is useful for spotting a config
that wins only in one regime.

---

## Stage 5 — Report  →  hand to `eiq-report-research`

Once you have a winner (or a clean negative result), invoke **`eiq-report-research`** to
produce the writeup: the hypothesis, what was tried each round, the metric table with
EAC leading (CORR alongside, AIMC where rounds have resolved), the deciding plots, why
the winner won, and what to try next. A negative
result is still worth reporting — it stops the next run from repeating it.

---

## Stage 6 — Submit  →  hand to `eiq-futures-submission`

Submit **only with the user's explicit go-ahead**, and only if the winner clears the bar
the design stage set (meaningful EAC and a real, non-overfit CORR). Then invoke
**`eiq-futures-submission`**, which owns:

- `create_model` (if this is a new model name),
- prediction formatting and the **`submit_futures_predictions`** call (the futures path —
  not the equities `submit_predictions`),
- post-submit confirmation via `get_round_diagnostics` / `get_scores`,
- and, if the user asks, the staking follow-through (`stake_on_model`,
  `get_stake_balance`, `claim_payout`).

---

## Defaults and principles

- **EAC is the primary experiment-selection lever.** **EAC** (Everest Alpha Contribution)
  is your unique signal beyond the **static benchmark** model (`eiq_minera_model`) — same
  contribution formula as AIMC, but measured against the static benchmark instead of the
  live stake-weighted ai-model. During research you don't have the live meta-model, so you
  **optimize EAC offline — it's the validation-time proxy for AIMC, the metric that
  actually pays once a round resolves.** Rank configs by EAC; CORR is the secondary
  accuracy component, and per-exped stability is the robustness check.
- **AIMC (AI Model Contribution) is the live payout metric EAC proxies for.** It is the
  same contribution formula measured against the *live* stake-weighted ai-model consensus,
  carries the 2.25 payout weight, and is the ultimate target — but it is only measurable
  once rounds resolve, so you chase it offline through EAC.
- **Payout is `0.75·CORR + 2.25·AIMC`, capped at ±25% of stake per round.** AIMC carries
  three times the weight of CORR, so **uniqueness pays more than raw accuracy** — keep the
  search pointed at differentiated alpha, not at chasing CORR. (If a doc you find quotes
  `0.50·CORR + 2.50·AIMC`, that is a stale-doc bug — use the formula above.)
- **Scout before you scale.** Always a downsampled-exped round first; full data only for
  survivors.
- **Iterate in rounds and stop at a plateau.** ~4-5 configs per round; two flat rounds
  means the idea is spent.
- **Benchmark against `ai_model` every time.** It is the comparison baseline;
  residualizing/neutralizing against it raises EAC/AIMC by lowering correlation with the
  static benchmark and the meta-model. No EAC/AIMC number is meaningful without the
  downloaded benchmark to compare against.
- **Respect the CV.** Exped-purged with embargo. The 20-day target needs a wide enough
  embargo to avoid look-ahead — let the design skill set it; never substitute plain k-fold.
- **Compute is metered.** Check `get_compute_credits` up front and let the budget bound
  the number of rounds.
- **Real data only.** Everything comes from the downloaded Everesteer datasets and the SDK/MCP —
  never fabricate features, targets, or scores.

## Worked example

> User: "Try training on benchmark-residualized targets — I think we're just echoing the ai_model."

1. **Orient** — `get_scores` + leaderboard show solid CORR, flat EAC. Bottleneck is
   differentiation, exactly the user's hunch.
2. **Design** (`eiq-experiment-design`) — three-round plan; dimension under test is the
   target transform (raw `target_everest_20` vs benchmark-residualized); EAC floor set.
3. **Implement** — none; the residualized-target path is templated. State so and skip.
4. **Scout** — ~5 configs on a 20%-exped sample. Raw-target configs land decent CORR but
   near-zero EAC (static-benchmark echo). One residualized config clears the EAC floor → promote it.
5. **Scale** — full expeds + wider features; EAC holds and CORR stays sane (per-exped
   stability corroborates) → winner.
6. **Report** (`eiq-report-research`) — table with EAC leading, residualization plot,
   rationale.
7. **Submit** (`eiq-futures-submission`) — on the user's OK, `create_model` if needed,
   then `submit_futures_predictions`; confirm via `get_round_diagnostics`.

<!-- NOTE: the exact EAC floor and embargo width are owned by eiq-experiment-design;
     this orchestrator intentionally does not hardcode them. -->
