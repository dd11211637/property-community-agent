"""Opt-in 20-concurrency latency check for an integrated test environment."""

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def main() -> None:
    base_url = os.environ["PERFORMANCE_BASE_URL"].rstrip("/")
    bearer_token = os.environ["PERFORMANCE_AUTH_TOKEN"]
    work_order_id = os.environ["PERFORMANCE_WORK_ORDER_ID"]
    request_count = int(os.getenv("PERFORMANCE_REQUEST_COUNT", "100"))
    url = f"{base_url}/api/work-orders/{work_order_id}"
    headers = {"Authorization": f"Bearer {bearer_token}"}

    def request_once(_: int) -> float:
        started = time.perf_counter()
        response = httpx.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return time.perf_counter() - started

    with ThreadPoolExecutor(max_workers=20) as executor:
        durations = list(executor.map(request_once, range(request_count)))

    p95 = statistics.quantiles(durations, n=100)[94]
    print(f"requests={request_count} concurrency=20 p95={p95:.3f}s")
    if p95 > 2:
        raise SystemExit("P95 exceeded the PRD threshold of 2 seconds")


if __name__ == "__main__":
    main()
