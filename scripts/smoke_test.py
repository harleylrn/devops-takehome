"""HTTP-only acceptance check; safe to use against ingress or a local API."""

import argparse
import hashlib
import sys
import time
import uuid

import httpx


def run(base_url, timeout=60):
    prompt = "DevOps smoke test"
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=5, trust_env=False) as client:
        assert client.post("/jobs", json={"prompt": " "}).status_code == 422
        assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404
        accepted = client.post("/jobs", json={"prompt": prompt})
        assert accepted.status_code == 202, f"submission returned {accepted.status_code}"
        job_id = accepted.json()["job_id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = client.get(f"/jobs/{job_id}")
            response.raise_for_status()
            state = response.json()
            assert state["status"] != "failed", f"job failed: {state['error']}"
            if state["status"] == "succeeded":
                expected = f"mock-result:{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
                assert state["result"] == expected, "unexpected result"
                print(f"PASS: validation, lookup, submission, and completion ({job_id})")
                return
            time.sleep(0.25)
        raise TimeoutError(f"job {job_id} did not complete within {timeout}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("timeout must be between 1 and 600 seconds")
    try:
        run(args.base_url, args.timeout)
    except (AssertionError, TimeoutError, httpx.HTTPError, KeyError, ValueError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
