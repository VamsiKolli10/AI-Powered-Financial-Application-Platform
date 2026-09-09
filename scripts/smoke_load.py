"""A quick load smoke test against a running Gateway.

    python -m scripts.smoke_load --url http://localhost:8000 --requests 200 --concurrency 20

Not a substitute for the Phase 8 load suite; this is the fast check that the edge holds
up and that latency has not regressed by an order of magnitude. Reports p50/p95/p99, the
error rate, and how many requests were shed by the rate limiter (429s are a healthy
response under load, not failures).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections import Counter

import httpx

DEFAULT_PASSWORD = "smoke-test-password"


async def _authenticate(client: httpx.AsyncClient, base_url: str, email: str) -> str:
    payload = {"email": email, "password": DEFAULT_PASSWORD, "full_name": "Smoke Test"}
    response = await client.post(f"{base_url}/api/v1/auth/register", json=payload)
    if response.status_code == 409:
        response = await client.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": email, "password": DEFAULT_PASSWORD},
        )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def _worker(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    headers: dict,
    queue: asyncio.Queue,
    latencies: list[float],
    statuses: Counter,
) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        started = time.perf_counter()
        try:
            response = await client.get(f"{base_url}{path}", headers=headers)
            statuses[response.status_code] += 1
        except httpx.HTTPError as exc:
            statuses[type(exc).__name__] += 1
        finally:
            latencies.append((time.perf_counter() - started) * 1000)
            queue.task_done()


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[index]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Gateway smoke load test")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--path", default="/api/v1/transactions")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--email", default="smoke@example.com")
    parser.add_argument("--max-p95-ms", type=float, default=0.0)
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=20.0) as client:
        token = await _authenticate(client, args.url, args.email)
        headers = {"Authorization": f"Bearer {token}"}

        queue: asyncio.Queue = asyncio.Queue()
        for _ in range(args.requests):
            queue.put_nowait(None)

        latencies: list[float] = []
        statuses: Counter = Counter()
        started = time.perf_counter()
        await asyncio.gather(
            *(
                _worker(client, args.url, args.path, headers, queue, latencies, statuses)
                for _ in range(args.concurrency)
            )
        )
        elapsed = time.perf_counter() - started

    ok = statuses.get(200, 0)
    shed = statuses.get(429, 0)
    errors = sum(count for status, count in statuses.items() if status not in (200, 429))

    print(f"\n{args.requests} requests at concurrency {args.concurrency} in {elapsed:.2f}s")
    print(f"  throughput : {args.requests / elapsed:,.0f} req/s")
    print(f"  ok         : {ok}")
    print(f"  shed (429) : {shed}")
    print(f"  errors     : {errors}  {dict(statuses)}")
    print(
        f"  latency ms : p50 {_percentile(latencies, 0.5):.1f} "
        f"p95 {_percentile(latencies, 0.95):.1f} "
        f"p99 {_percentile(latencies, 0.99):.1f} "
        f"mean {statistics.mean(latencies):.1f}"
    )

    if errors:
        print("\nFAIL: requests failed for reasons other than rate limiting.")
        return 1
    p95 = _percentile(latencies, 0.95)
    if args.max_p95_ms and p95 > args.max_p95_ms:
        print(f"\nFAIL: p95 {p95:.1f}ms exceeds the {args.max_p95_ms:.0f}ms budget.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
