"""Frozen held-out evaluation of the actual categorization workflow.

Rules run offline by default. --live explicitly opts into provider calls using private
settings. Errors fall back to rules, as production does, and are counted separately.
No request credentials, raw exceptions or provider response bodies are reported.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from libs.common.config import get_settings
from libs.common.text import normalize_description
from libs.db.categories import CATEGORY_SLUGS
from libs.llm_client.errors import LLMError
from libs.llm_client.factory import build_categorizer
from services.transactions.categorizer import classify

DATASET = Path(__file__).resolve().parents[1] / "libs/llm_client/evals/held_out_transactions.json"


def metrics(rows: list[dict]) -> dict:
    categories = {}
    for slug in sorted(CATEGORY_SLUGS):
        subset = [r for r in rows if r["expected"] == slug]
        correct = sum(r["correct"] for r in subset)
        categories[slug] = {
            "correct": correct,
            "support": len(subset),
            "accuracy": correct / len(subset) if subset else None,
        }
    latencies = sorted(r["latency_ms"] for r in rows)
    return {
        "accuracy": sum(r["correct"] for r in rows) / len(rows),
        "macro_accuracy": statistics.mean(
            c["accuracy"] for c in categories.values() if c["support"]
        ),
        "per_category": categories,
        "latency_ms": {
            "p50": statistics.median(latencies),
            "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        },
        "fallback_rate": sum(r["source"] == "rules_fallback" for r in rows) / len(rows),
        "sources": dict(Counter(r["source"] for r in rows)),
        "outcomes": rows,
    }


async def evaluate(*, live: bool, gap: float) -> dict:
    samples = json.loads(DATASET.read_text())["samples"]
    result = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "samples": len(samples),
        "limitations": (
            "Small synthetic semantic challenge set with provisional labels; "
            "not a population estimate. Provider routing and latency can vary. "
            "Empty cache at start; within-run cache hits are reported. No tuning on this set."
        ),
    }
    classifier = build_categorizer(get_settings()) if live else None
    if live and classifier is None:
        raise RuntimeError("Live evaluation needs an enabled provider in private settings")
    if live:
        result["model"] = get_settings().llm_model
    for mode in (["rules", "ai_workflow"] if live else ["rules"]):
        rows = []
        for index, sample in enumerate(samples):
            description = normalize_description(sample["description"])
            amount = Decimal(str(sample["amount"]))
            start = time.perf_counter()
            error = None
            try:
                classification = (
                    await classifier(description, amount)
                    if mode == "ai_workflow"
                    else classify(description, amount)
                )
                source = classification.source
            except LLMError as exc:
                classification = classify(description, amount)
                source, error = "rules_fallback", type(exc).__name__
            latency = (time.perf_counter() - start) * 1000
            rows.append(
                {
                    "description": sample["description"],
                    "expected": sample["expected"],
                    "predicted": classification.category_slug,
                    "correct": classification.category_slug == sample["expected"],
                    "source": source,
                    "latency_ms": round(latency, 2),
                    "error_type": error,
                }
            )
            if mode == "ai_workflow":
                print(f"Evaluated synthetic sample {index+1}/{len(samples)}", flush=True)
                if index + 1 < len(samples):
                    await asyncio.sleep(max(0, gap - latency / 1000))
        result[mode] = metrics(rows)
    if classifier is not None:
        await classifier.client.completer._client.close()
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--min-gap", type=float, default=6.2)
    parser.add_argument("--output", type=Path, default=Path("docs/evaluations/held-out-rules.json"))
    args = parser.parse_args()
    result = await evaluate(live=args.live, gap=max(0, args.min_gap))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    for mode in ("rules", "ai_workflow"):
        if mode in result:
            value = result[mode]
            print(
                f"{mode}: accuracy={value['accuracy']:.1%}, "
                f"fallback={value['fallback_rate']:.1%}, "
                f"p95={value['latency_ms']['p95']:.0f}ms"
            )


if __name__ == "__main__":
    asyncio.run(main())
