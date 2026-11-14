"""Standalone offline neuron steering CLI with an isolated bounded worker."""

import argparse
import json
import os
import pathlib
import select
import subprocess
import sys
import time

from . import __version__
from .jobs import validate_job
from .process import bounded_run
from .steering import MAX_PROMPT_BYTES, NeuronSteerError, formula
from .storage import strict_json


def parser():
    root = argparse.ArgumentParser(
        prog="neuron-steer",
        description="Explicit local Transformers neuron steering; temporary hooks, no weight edits.",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="operation", required=True)
    for operation in ("generate", "evaluate"):
        p = commands.add_parser(operation)
        p.add_argument(
            "--model", required=True, help="Existing local Transformers model directory"
        )
        p.add_argument(
            "--neuron",
            action="append",
            required=True,
            dest="neurons",
            metavar="LAYER:NEURON:DOSE",
        )
        p.add_argument(
            "--device", choices=("auto", "cpu", "mps", "cuda"), default="auto"
        )
        p.add_argument("--max-tokens", type=int, default=64)
        p.add_argument("--temperature", type=float, default=0.0)
        p.add_argument("--context-size", type=int, default=0)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument(
            "--thinking",
            action="store_false",
            dest="no_thinking",
            help="Leave template thinking enabled where supported",
        )
        p.add_argument(
            "--timeout",
            type=float,
            default=900,
            help="Total command budget including stdin and child (1–86400 seconds)",
        )
        p.add_argument("--dry-run", action="store_true")
        if operation == "generate":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--prompt")
            group.add_argument("--stdin", action="store_true")
        else:
            p.add_argument(
                "--prompts",
                default=str(
                    pathlib.Path(__file__).parent
                    / "prompts"
                    / "neuron_steer_prompts_100.json"
                ),
            )
            p.add_argument("--output", required=True)
            p.add_argument("--markdown-out")
            p.add_argument("--limit", type=int, default=0)
            p.add_argument("--resume", action="store_true")
    return root


def read_prompt(deadline):
    try:
        descriptor = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return sys.stdin.read(MAX_PROMPT_BYTES + 1)
    data = bytearray()
    while True:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([descriptor], [], [], left)[0]:
            raise NeuronSteerError("stdin deadline exceeded")
        chunk = os.read(descriptor, min(8192, MAX_PROMPT_BYTES + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_PROMPT_BYTES:
            raise NeuronSteerError("stdin prompt exceeds the byte limit")
    return data.decode("utf-8")


def main(argv=None):
    args = parser().parse_args(argv)
    started = time.monotonic()
    try:
        job = vars(args).copy()
        if not 1 <= args.timeout <= 86400:
            raise NeuronSteerError("--timeout must be 1–86400 seconds")
        deadline = started + args.timeout
        job["model"] = str(pathlib.Path(args.model).expanduser().resolve())
        if args.operation == "generate" and args.stdin:
            job["prompt"] = read_prompt(deadline)
        if args.operation == "evaluate":
            job["prompts"] = str(pathlib.Path(args.prompts).expanduser().resolve())
            job["output"] = str(pathlib.Path(args.output).expanduser().absolute())
            job["markdown_out"] = (
                str(pathlib.Path(args.markdown_out).expanduser().absolute())
                if args.markdown_out
                else str(pathlib.Path(job["output"]).with_suffix(".md"))
            )
        specs = validate_job(job)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "version": __version__,
                        "dry_run": True,
                        "job": job,
                        "targets": specs,
                        "formula": formula(),
                        "private_history_accessed": False,
                        "model_loaded": False,
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
            return 0
        result = bounded_run(
            [sys.executable, "-m", "neuron_steer._worker"],
            input=json.dumps(job, allow_nan=False),
            timeout=max(0.001, deadline - time.monotonic()),
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
            },
        )
        if result.stderr:
            # Diagnostics/progress are bounded by the child pipe limit.
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode:
            try:
                error_payload = strict_json(result.stdout)
                detail = (
                    error_payload.get("error", "worker failed")
                    if isinstance(error_payload, dict)
                    else "invalid worker diagnostic"
                )
            except ValueError:
                detail = "worker failed without a JSON diagnostic"
            raise NeuronSteerError(str(detail)[:1000])
        payload = strict_json(result.stdout)
        if not isinstance(payload, dict) or payload.get("error"):
            raise NeuronSteerError("invalid worker response")
        print(
            json.dumps(
                {"version": __version__, "private_history_accessed": False, **payload},
                indent=2,
                allow_nan=False,
            )
        )
        return 0
    except (
        NeuronSteerError,
        OSError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print("neuron-steer: " + str(exc)[:1000], file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("neuron-steer: interrupted", file=sys.stderr)
        return 130
