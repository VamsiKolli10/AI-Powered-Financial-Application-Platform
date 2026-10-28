# Categorization measurements

The original regression set remains at 57/60 (95.0%) for the rules engine. Its familiar
merchant examples are useful for detecting regressions, but do not establish performance
on unfamiliar descriptions.

The separate, frozen semantic challenge set has 34 synthetic examples covering all 17
categories. Merchant cache groups do not overlap the original set. Labels were authored
against the taxonomy and are **provisional**, not independently human-validated. Two own-account
transfer directions share a merchant cache group. Neither prompts nor rules were tuned
against this set during the evaluation.

| Run | Correct | Accuracy | Rules fallback | p95 classification latency |
| --- | --- | --- | --- | --- |
| Rules on challenge set | 4/34 | 11.8% | N/A | under 2 ms |
| Configured OpenRouter `openrouter/free` workflow | 33/34 | 97.1% | 0/34 | 6,280 ms |

The live run started with an empty in-memory cache. One transfer reused the preceding
classification; reported latency includes that reuse. The reported LLM source includes
cached LLM classifications. The router can choose a different underlying model on a later
run. Read the JSON for per-category support, accuracy, individual outcomes, latency and
dataset SHA-256. These numbers describe this small challenge set, not production accuracy.

The miss was a replacement debit-card charge, labeled Fees. Redaction reduced the description
to `replacement charge`; the model chose Other with low confidence. This illustrates a real
privacy/quality tradeoff and a case where user correction matters.

Reproduce offline with:

```bash
python -m scripts.eval_categorization --mode rules --min-accuracy 0.90
python -m scripts.eval_quality
```

Opt in to provider calls using private local settings:

```bash
python -m scripts.eval_quality --live --output docs/evaluations/held-out-live.json
python -m scripts.verify_provider --live
```

The default 6.2-second minimum gap respects a ten-requests-per-minute configuration.
Provider retries may add latency. Errors are counted as rules fallbacks and remain in the
accuracy denominator; there is no selection of only successful calls. No credentials,
provider error bodies or real user transactions are included in these reports.
