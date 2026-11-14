"""Private bounded subprocess entry point; model diagnostics go to stderr."""

import contextlib
import json
import sys
from .jobs import run_job


def main():
    try:
        source = sys.stdin.read(100000)
        if len(source) >= 100000:
            raise ValueError("worker request is too large")
        job = json.loads(
            source,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("non-finite JSON")
            ),
        )
        with contextlib.redirect_stdout(sys.stderr):
            response = run_job(job)
        print(json.dumps(response, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:1000]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
