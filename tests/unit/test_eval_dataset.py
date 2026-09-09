"""Guards the labeled sample and the accuracy it produces."""

from decimal import Decimal

from libs.db.categories import CATEGORY_SLUGS
from scripts.eval_categorization import SAMPLE_PATH, evaluate, load_samples
from services.transactions import categorizer as rules

RULES_ACCURACY_FLOOR = 0.90


def test_every_label_is_a_real_category():
    for sample in load_samples(SAMPLE_PATH):
        assert sample["expected"] in CATEGORY_SLUGS


def test_sample_covers_most_of_the_taxonomy():
    labels = {s["expected"] for s in load_samples(SAMPLE_PATH)}
    assert len(labels) >= 12


async def test_rules_engine_meets_its_accuracy_floor():
    samples = load_samples(SAMPLE_PATH)
    outcomes = await evaluate(samples, lambda d, a: rules.classify(d, a))
    accuracy = sum(o.correct for o in outcomes) / len(outcomes)
    assert accuracy >= RULES_ACCURACY_FLOOR, f"rules accuracy regressed to {accuracy:.1%}"


def test_amounts_have_the_right_sign():
    inflow_labels = {"income", "transfers"}
    for sample in load_samples(SAMPLE_PATH):
        if Decimal(str(sample["amount"])) > 0:
            assert sample["expected"] in inflow_labels
