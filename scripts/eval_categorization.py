"""Measure categorization accuracy against the labeled sample.

    python -m scripts.eval_categorization                 # rules engine only (no API key needed)
    python -m scripts.eval_categorization --mode llm      # calls the provider
    python -m scripts.eval_categorization --mode both     # compare side by side

Run this before and after changing a prompt: without a number, prompt edits are guesswork.
Exits non-zero if accuracy falls below --min-accuracy, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from libs.common.classification import Classification
from libs.common.config import get_settings
from libs.common.text import normalize_description
from libs.llm_client.errors import LLMError
from libs.llm_client.factory import build_categorizer
from services.transactions import categorizer as rules

SAMPLE_PATH = (
    Path(__file__).resolve().parents[1] / "libs/llm_client/evals/labeled_transactions.json"
)


# Either classifier shape: the rules engine is sync, the LLM one is async.
Classifier = Callable[[str, Decimal], Classification | Awaitable[Classification]]


@dataclass
class Outcome:
    description: str
    expected: str
    predicted: str
    correct: bool


def load_samples(path: Path) -> list[dict]:
    return json.loads(path.read_text())["samples"]


async def evaluate(samples: list[dict], classify: Classifier) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for sample in samples:
        normalized = normalize_description(sample["description"])
        amount = Decimal(str(sample["amount"]))
        try:
            outcome = classify(normalized, amount)
            result = await outcome if isinstance(outcome, Awaitable) else outcome
            predicted = result.category_slug
        except LLMError as exc:
            predicted = f"error:{type(exc).__name__}"
        outcomes.append(
            Outcome(
                description=sample["description"],
                expected=sample["expected"],
                predicted=predicted,
                correct=predicted == sample["expected"],
            )
        )
    return outcomes


def report(label: str, outcomes: list[Outcome], *, show_misses: bool) -> float:
    correct = sum(o.correct for o in outcomes)
    accuracy = correct / len(outcomes) if outcomes else 0.0

    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in outcomes:
        per_category[o.expected][1] += 1
        per_category[o.expected][0] += int(o.correct)

    print(f"\n=== {label} ===")
    print(f"accuracy: {accuracy:.1%}  ({correct}/{len(outcomes)})")
    weakest = sorted(per_category.items(), key=lambda kv: kv[1][0] / kv[1][1])[:5]
    print("weakest categories:")
    for slug, (hits, total) in weakest:
        print(f"  {slug:<16} {hits}/{total}")
    if show_misses:
        misses = [o for o in outcomes if not o.correct]
        if misses:
            print("misses:")
            for o in misses:
                print(f"  {o.description[:42]:<44} expected={o.expected:<15} got={o.predicted}")
    return accuracy


async def main() -> int:
    parser = argparse.ArgumentParser(description="Categorization accuracy eval")
    parser.add_argument("--mode", choices=["rules", "llm", "both"], default="rules")
    parser.add_argument("--min-accuracy", type=float, default=0.0)
    parser.add_argument("--samples", type=Path, default=SAMPLE_PATH)
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    samples = load_samples(args.samples)
    accuracies: list[float] = []

    if args.mode in ("rules", "both"):
        outcomes = await evaluate(samples, lambda d, a: rules.classify(d, a))
        accuracies.append(report("rules engine", outcomes, show_misses=args.show_misses))

    if args.mode in ("llm", "both"):
        llm = build_categorizer(get_settings())
        if llm is None:
            print("\nLLM categorizer is not configured (set OPENAI_API_KEY and LLM_ENABLED).")
            return 1
        outcomes = await evaluate(samples, llm)
        accuracies.append(report("llm", outcomes, show_misses=args.show_misses))
        print(f"\ncache hit rate during eval: {llm.cache.hit_rate:.1%}")

    best = max(accuracies) if accuracies else 0.0
    if args.min_accuracy and best < args.min_accuracy:
        print(f"\nFAIL: {best:.1%} is below the {args.min_accuracy:.1%} threshold.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
