import json

from libs.common.text import cache_key, normalize_description
from libs.db.categories import CATEGORY_SLUGS
from scripts.eval_categorization import SAMPLE_PATH
from scripts.eval_quality import DATASET, metrics


def test_held_out_covers_every_category_without_merchant_overlap():
    samples = json.loads(DATASET.read_text())["samples"]
    assert {s["expected"] for s in samples} == CATEGORY_SLUGS
    old = {
        cache_key(normalize_description(s["description"]))
        for s in json.loads(SAMPLE_PATH.read_text())["samples"]
    }
    new = [cache_key(normalize_description(s["description"])) for s in samples]
    assert not old.intersection(new)
    # Two directions of an own-account transfer intentionally share a merchant key.
    # Shared groups must have consistent labels; report cache hits explicitly.
    for group in set(new):
        assert (
            len({s["expected"] for s, key in zip(samples, new, strict=True) if key == group}) == 1
        )


def test_report_counts_fallbacks_and_failures_in_accuracy():
    result = metrics(
        [
            {"expected": "groceries", "correct": True, "source": "llm", "latency_ms": 10},
            {
                "expected": "groceries",
                "correct": False,
                "source": "rules_fallback",
                "latency_ms": 100,
            },
        ]
    )
    assert result["accuracy"] == 0.5 and result["fallback_rate"] == 0.5
    assert result["per_category"]["groceries"]["support"] == 2
