"""Opt-in live provider checks with synthetic facts and status-only output."""

import argparse
import asyncio

from libs.common.config import get_settings
from libs.llm_client.factory import build_responder, build_summarizer
from libs.llm_client.grounding import valid_rewrite


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("SKIP live provider checks (opt in with --live)")
        return
    settings = get_settings()
    responder = build_responder(settings)
    summarizer = build_summarizer(settings)
    assert responder is not None and summarizer is not None, "Provider is not configured"
    facts = {
        "period": "October 2026",
        "currency": "USD",
        "total_spend": 59.62,
        "by_category": [{"category": "Groceries", "amount": 59.62}],
    }
    draft = "You spent $59.62 in October 2026. Groceries ($59.62)."
    try:
        reply, source, reason = await responder.respond(
            question="Summarize my spending", facts=facts, draft=draft
        )
        assert valid_rewrite(reply, draft=draft, facts=facts)
        assert reason not in {
            "provider_unavailable",
            "provider_timeout",
            "rate_limited",
        }, "Provider did not complete"
        assert source == "llm", "No validated live assistant response was accepted"
        summary, summary_source = await summarizer.summarize(facts)
        assert valid_rewrite(summary, draft=summarizer._fallback(facts), facts=facts)
        assert summary_source == "llm", "No validated live summary response was accepted"
        if summary_source == "llm":
            repeated = await summarizer.summarize(facts)
            assert repeated == (summary, "cache")
        print(
            f"PASS live assistant numeric grounding ({source}); "
            f"summary grounding ({summary_source})"
        )
    finally:
        await responder.client.completer._client.close()
        await summarizer.client.completer._client.close()


if __name__ == "__main__":
    asyncio.run(main())
