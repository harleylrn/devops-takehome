"""Bounded job submission and completion measurement, not an unbounded stress test."""

import argparse
import asyncio
import json
import time

import httpx


async def run(args):
    semaphore = asyncio.Semaphore(args.concurrency)
    outcomes = {"succeeded": 0, "failed": 0, "submission_errors": 0, "timed_out": 0}
    start = time.monotonic()
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=5, trust_env=False
    ) as client:

        async def one(index):
            async with semaphore:
                try:
                    response = await client.post("/jobs", json={"prompt": f"Load job {index}"})
                    response.raise_for_status()
                    job_id = response.json()["job_id"]
                except (httpx.HTTPError, ValueError, KeyError):
                    outcomes["submission_errors"] += 1
                    return
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    try:
                        response = await client.get(f"/jobs/{job_id}")
                        response.raise_for_status()
                        status = response.json()["status"]
                        if status in ("succeeded", "failed"):
                            outcomes[status] += 1
                            return
                    except (httpx.HTTPError, KeyError, ValueError):
                        pass
                    await asyncio.sleep(0.5)
                outcomes["timed_out"] += 1

        await asyncio.gather(*(one(i) for i in range(args.jobs)))
    print(json.dumps({**outcomes, "elapsed_seconds": round(time.monotonic() - start, 2)}))
    return int(outcomes["succeeded"] != args.jobs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--jobs", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not (1 <= args.jobs <= 1000 and 1 <= args.concurrency <= 50 and 1 <= args.timeout <= 300):
        parser.error("jobs: 1–1000; concurrency: 1–50; timeout per job: 1–300 seconds")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
