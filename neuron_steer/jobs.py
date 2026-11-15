"""Generation and checkpointed paired evaluation, independent of CallAI."""

import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import pathlib
import sys

from . import __version__
from .evaluation import load_prompts, markdown, metric_row, summarize
from .neuronpedia import normalize_references
from .runtime import LocalRuntime
from .steering import (
    NeuronSteerError,
    parse_specs,
    validate_generation_options,
    validate_prompt,
)
from .storage import atomic_text, read_json, write_json


def validate_job(job):
    if not isinstance(job, dict):
        raise NeuronSteerError("job must be an object")
    if job.get("operation") not in ("generate", "evaluate"):
        raise NeuronSteerError("unsupported operation")
    required = (
        "max_tokens",
        "temperature",
        "context_size",
        "seed",
        "timeout",
        "device",
        "model",
        "no_thinking",
    )
    if any(key not in job for key in required):
        raise NeuronSteerError("job is missing required settings")
    if (
        not isinstance(job["model"], str)
        or not job["model"].strip()
        or type(job["no_thinking"]) is not bool
    ):
        raise NeuronSteerError("invalid model path or thinking setting")
    if not isinstance(job.get("neurons"), list):
        raise NeuronSteerError("neuron targets must be a list")
    job["references"] = normalize_references(job.get("references", []))
    specs = parse_specs(job.get("neurons"))
    validate_generation_options(
        **{k: job[k] for k in ("max_tokens", "temperature", "context_size", "seed")}
    )
    if (
        not isinstance(job.get("timeout"), (int, float))
        or not 1 <= job["timeout"] <= 86400
    ):
        raise NeuronSteerError("--timeout must be 1–86400 seconds")
    if job.get("device") not in ("auto", "cpu", "mps", "cuda"):
        raise NeuronSteerError("unsupported device")
    if job["operation"] == "generate":
        validate_prompt(job.get("prompt"))
    else:
        if type(job.get("limit")) is not int or not 0 <= job["limit"] <= 10000:
            raise NeuronSteerError("--limit must be 0–10000")
        if (
            any(
                not isinstance(job.get(key), str) or not job[key]
                for key in ("prompts", "output", "markdown_out")
            )
            or type(job.get("resume")) is not bool
        ):
            raise NeuronSteerError(
                "evaluation requires paths and a boolean resume setting"
            )
    return specs


def checkpoint_identity(job, prompts):
    model = pathlib.Path(job["model"]).expanduser().resolve()
    # Detect replaced local weights/configs without reading gigabytes of tensor data.
    files = []
    for file in sorted(model.glob("*")):
        if file.is_file() and (
            file.suffix == ".safetensors"
            or file.name
            in (
                "config.json",
                "generation_config.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "special_tokens_map.json",
            )
            or file.name.endswith(".safetensors.index.json")
        ):
            stat = file.stat()
            files.append((file.name, stat.st_size, stat.st_mtime_ns))
    identity = {
        k: job[k]
        for k in (
            "model",
            "neurons",
            "max_tokens",
            "temperature",
            "seed",
            "context_size",
            "device",
            "no_thinking",
        )
    }
    versions = {}
    for name in ("torch", "transformers", "safetensors"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    identity.update(
        references=normalize_references(job.get("references", [])),
        prompts=prompts,
        model_files=files,
        format_version=2,
        package_version=__version__,
        runtime_versions=versions,
        python=sys.version.split()[0],
        markdown_out=str(pathlib.Path(job["markdown_out"]).resolve()),
    )
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def run_job(job):
    specs = validate_job(job)
    model = pathlib.Path(job["model"]).expanduser().resolve()
    if not model.is_dir():
        raise NeuronSteerError("model must point to an existing local directory")
    if job["operation"] == "evaluate":
        return evaluate(job, specs)
    runtime = LocalRuntime(model, job["device"])
    result = runtime.generate(job["prompt"], specs, **generation_options(job))
    return {
        "operation": "generate",
        "runtime": runtime.info,
        "neuronpedia_references": job["references"],
        "references_are_annotations": True,
        **result,
    }


def generation_options(job):
    return {
        key: job[key]
        for key in ("max_tokens", "temperature", "context_size", "seed", "no_thinking")
    }


def validate_output_paths(job):
    model = pathlib.Path(job["model"]).resolve()
    prompt = pathlib.Path(job["prompts"]).resolve()
    output, md_out = (pathlib.Path(job[key]) for key in ("output", "markdown_out"))
    if output.resolve() == md_out.resolve() or (
        output.exists() and md_out.exists() and output.samefile(md_out)
    ):
        raise NeuronSteerError("JSON and Markdown outputs must be different paths")
    for target in (output, md_out):
        if target.is_symlink():
            raise NeuronSteerError("report paths must not be symlinks")
        if target.resolve() == prompt or (
            target.exists() and prompt.exists() and target.samefile(prompt)
        ):
            raise NeuronSteerError("outputs must not overwrite the prompt file")
        if target.resolve().is_relative_to(model):
            raise NeuronSteerError("reports must be outside the model directory")
        if target.exists():
            for file in model.rglob("*"):
                if file.is_file() and target.samefile(file):
                    raise NeuronSteerError("reports must not alias model files")
    resuming = job["resume"] and output.exists()
    if not resuming and (output.exists() or md_out.exists()):
        raise NeuronSteerError(
            "output already exists; use --resume or choose new report paths"
        )
    return output, md_out


@contextlib.contextmanager
def checkpoint_lock(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:24]
    lock_path = output.parent / (".neuron-steer-" + key + ".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise NeuronSteerError("cannot open checkpoint lock") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise NeuronSteerError(
                "checkpoint is already in use by another evaluation"
            ) from exc
        yield
    finally:
        os.close(fd)


def evaluate(job, specs):
    output, md_out = validate_output_paths(job)
    with contextlib.ExitStack() as locks:
        for path in sorted((output, md_out), key=lambda p: str(p.resolve())):
            locks.enter_context(checkpoint_lock(path))
        validate_output_paths(job)
        return evaluate_locked(job, specs)


def validate_records(records, prompts, maximum):
    if not isinstance(records, list):
        raise NeuronSteerError("checkpoint records must be a list")
    seen = set()
    for row in records:
        index = row.get("index") if isinstance(row, dict) else None
        if type(index) is not int or index in seen or not 1 <= index <= len(prompts):
            raise NeuronSteerError("invalid or duplicate checkpoint record")
        if any(
            row.get(key) != prompts[index - 1][key] for key in ("prompt", "category")
        ):
            raise NeuronSteerError("checkpoint prompt/category mismatch")
        for side in ("baseline", "steered"):
            result = row.get(side)
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("content"), str)
                or len(result["content"].encode("utf-8")) > 8 * 1024 * 1024
            ):
                raise NeuronSteerError("invalid checkpoint generation content")
            if (
                type(result.get("tokens")) is not int
                or not 0 <= result["tokens"] <= maximum
            ):
                raise NeuronSteerError("invalid checkpoint token count")
            elapsed = result.get("elapsed_seconds")
            if (
                type(elapsed) not in (int, float)
                or not math.isfinite(elapsed)
                or elapsed < 0
            ):
                raise NeuronSteerError("invalid checkpoint elapsed time")
        if row.get("metrics") != metric_row(
            row["baseline"]["content"], row["steered"]["content"]
        ):
            raise NeuronSteerError("checkpoint metrics do not match generation text")
        seen.add(index)


def evaluate_locked(job, specs):
    prompts = load_prompts(pathlib.Path(job["prompts"]))
    if job["limit"]:
        if job["limit"] > len(prompts):
            raise NeuronSteerError("--limit exceeds the prompt count")
        prompts = prompts[: job["limit"]]
    output = pathlib.Path(job["output"])
    md_out = pathlib.Path(job["markdown_out"])
    fingerprint = checkpoint_identity(job, prompts)
    records = []
    if output.exists():
        if not job["resume"]:
            raise NeuronSteerError(
                "output already exists; use --resume or choose a new output"
            )
        existing = read_json(output, 128 * 1024 * 1024)
        if not isinstance(existing, dict) or existing.get("version") != 2:
            raise NeuronSteerError(
                "unsupported checkpoint format; start a new v2 report"
            )
        if existing.get("fingerprint") != fingerprint:
            raise NeuronSteerError(
                "resume settings, model files or prompts differ from the checkpoint"
            )
        records = existing.get("records")
        validate_records(records, prompts, job["max_tokens"])
        if not isinstance(existing.get("runtime"), dict):
            raise NeuronSteerError("checkpoint runtime must be an object")
    done = {row["index"] for row in records}
    runtime = (
        LocalRuntime(job["model"], job["device"]) if len(done) < len(prompts) else None
    )
    if records and runtime is not None and existing["runtime"] != runtime.info:
        raise NeuronSteerError("resolved runtime differs from the checkpoint")
    payload = {
        "version": 2,
        "fingerprint": fingerprint,
        "model": job["model"],
        "targets": job["neurons"],
        "neuronpedia_references": job["references"],
        "references_are_annotations": True,
        "baseline": "no steering hooks",
        "runtime": runtime.info if runtime else existing["runtime"],
        "private_history_accessed": False,
        "hosted_text_submitted": False,
        "records": records,
    }
    for row in prompts:
        if row["index"] in done:
            continue
        print(
            f"[{row['index']}/{len(prompts)}] {row['category']}",
            file=sys.stderr,
            flush=True,
        )
        before = runtime.generate(row["prompt"], [], **generation_options(job))
        after = runtime.generate(row["prompt"], specs, **generation_options(job))
        records.append(
            {
                **row,
                "baseline": before,
                "steered": after,
                "metrics": metric_row(before["content"], after["content"]),
            }
        )
        records.sort(key=lambda r: r["index"])
        payload["summary"] = summarize(records)
        write_json(output, payload)
    payload["summary"] = summarize(records)
    write_json(output, payload)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(md_out, markdown(payload))
    return {
        "operation": "evaluate",
        "output": str(output),
        "markdown": str(md_out),
        "summary": payload["summary"],
    }
