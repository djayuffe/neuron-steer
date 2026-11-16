"""Local steering and explicit Neuronpedia research, using bounded workers."""

import argparse
import json
import os
import pathlib
import select
import subprocess
import sys
import time

from . import __version__, neuronpedia
from .jobs import evaluation_preflight, validate_job
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
        if operation == "generate":
            target = p.add_mutually_exclusive_group(required=True)
            target.add_argument(
                "--baseline",
                action="store_true",
                help="Generate without calibration or steering hooks",
            )
            target.add_argument(
                "--neuron",
                action="append",
                dest="neurons",
                default=[],
                metavar="LAYER:NEURON:DOSE",
            )
        else:
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
        p.add_argument(
            "--system-role", choices=("auto", "merge", "strict"), default="auto"
        )
        p.add_argument(
            "--chat-template",
            help="Named tokenizer template; default selects its default entry",
        )
        p.add_argument(
            "--calibration", choices=("skip-first", "all"), default="skip-first"
        )
        p.add_argument(
            "--asset-identity",
            choices=("metadata", "sha256"),
            default="metadata",
            help="Optional content hashes for model/tokenizer assets; hashing shares the command timeout",
        )
        p.add_argument("--dry-run", action="store_true")
        p.add_argument(
            "--reference",
            dest="references",
            action="append",
            default=[],
            help="Neuronpedia feature URL, stored as a research annotation; never fetched locally",
        )
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
            p.add_argument(
                "--seeds", help="Comma-separated unique seeds; defaults to --seed"
            )
            p.add_argument(
                "--dose-scales",
                default="1",
                help="Comma-separated multipliers; resulting doses must remain ±10",
            )
            p.add_argument(
                "--control",
                dest="controls",
                action="append",
                choices=("zero", "random", "reversed", "ablation"),
                default=[],
            )
            p.add_argument(
                "--order", choices=("alternate", "baseline-first"), default="alternate"
            )
    np = commands.add_parser(
        "neuronpedia",
        help="Explicit Neuronpedia API operations; separate from local hooks",
    )
    np.add_argument("action", choices=neuronpedia.OPERATIONS)
    np.add_argument("--model")
    np.add_argument("--source")
    np.add_argument("--source-set")
    np.add_argument("--index", type=int)
    np.add_argument("--url", help="Official feature URL instead of model/source/index")
    np.add_argument(
        "--stdin",
        action="store_true",
        help="Explicit text for search, activate, top or steer",
    )
    np.add_argument("--endpoint", default=neuronpedia.ORIGIN)
    np.add_argument(
        "--api-key-env", help="Environment variable name, never a key value"
    )
    np.add_argument(
        "--ca-file",
        help="CA bundle for loopback HTTPS; certificate verification stays enabled",
    )
    np.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Total command budget, including stdin/network (1–300 seconds)",
    )
    np.add_argument("--offset", type=int, default=0)
    np.add_argument("--limit", type=int, default=20)
    np.add_argument("--max-tokens", type=int, default=48)
    np.add_argument("--temperature", type=float, default=0)
    np.add_argument("--strength", type=float, default=1)
    np.add_argument("--seed", type=int, default=16)
    np.add_argument("--dry-run", action="store_true")
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
        if not 1 <= args.timeout <= 86400:
            raise NeuronSteerError("--timeout must be 1–86400 seconds")
        deadline = started + args.timeout
        if args.operation == "neuronpedia":
            neuronpedia.build_request(args)
            text = read_prompt(deadline) if args.stdin else None
            if args.dry_run or args.action == "link":
                reply = neuronpedia.run(args, deadline, lambda budget: text)
                print(
                    json.dumps(
                        {"version": __version__, **reply}, indent=2, allow_nan=False
                    )
                )
                return 0
            job = {**vars(args), "text": text}
        else:
            job = vars(args).copy()
            model_path = pathlib.Path(args.model).expanduser()
            job["model"] = str(model_path.resolve(strict=model_path.is_symlink()))
            if args.operation == "evaluate":
                job["prompts"] = str(pathlib.Path(args.prompts).expanduser().resolve())
                job["output"] = str(pathlib.Path(args.output).expanduser().absolute())
                job["markdown_out"] = (
                    str(pathlib.Path(args.markdown_out).expanduser().absolute())
                    if args.markdown_out
                    else str(pathlib.Path(job["output"]).with_suffix(".md"))
                )
            validate_job(job, require_prompt=False)
            if args.operation == "generate" and args.stdin:
                job["prompt"] = read_prompt(deadline)
            specs = validate_job(job)
            preflight = {
                "model_directory_exists": pathlib.Path(job["model"]).is_dir(),
                "model_dimensions_validated": False,
            }
            if args.operation == "evaluate" and args.dry_run:
                selected = evaluation_preflight(job)
                preflight["prompts_selected"] = len(selected)
                from .experiments import conditions, trials

                planned = trials(selected, job)
                preflight["trials"] = len(planned)
                preflight["generations"] = sum(
                    len(conditions(specs, row["dose_scale"], job["controls"]))
                    for row in planned
                )
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "version": __version__,
                            "dry_run": True,
                            "job": job,
                            "targets": specs,
                            "preflight": preflight,
                            "formula": formula(job.get("calibration", "skip-first")),
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
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print("neuron-steer: " + str(exc)[:1000], file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("neuron-steer: interrupted", file=sys.stderr)
        return 130
