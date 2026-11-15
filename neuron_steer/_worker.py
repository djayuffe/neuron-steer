"""Private bounded subprocess entry point; model diagnostics go to stderr."""

import argparse
import contextlib
import json
import sys
import time

from . import neuronpedia
from .jobs import run_job
from .storage import strict_json


def main():
    try:
        source = sys.stdin.read(100000)
        if len(source) >= 100000:
            raise ValueError("worker request is too large")
        job = strict_json(source, 100000)
        with contextlib.redirect_stdout(sys.stderr):
            if isinstance(job, dict) and job.get("operation") == "neuronpedia":
                args = argparse.Namespace(**job)
                response = neuronpedia.run(
                    args,
                    time.monotonic() + args.timeout,
                    lambda deadline: job.get("text"),
                )
            else:
                response = run_job(job)
        print(json.dumps(response, allow_nan=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — isolated worker preserves JSON diagnostics from optional runtimes
        print(json.dumps({"error": str(exc)[:1000]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
