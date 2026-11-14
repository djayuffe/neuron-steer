"""Generation and checkpointed paired evaluation, independent of CallAI."""

import hashlib
import json
import pathlib
import sys
from .runtime import LocalRuntime
from .evaluation import load_prompts, metric_row, summarize, markdown, write_json
from .steering import (
    NeuronSteerError,
    parse_specs,
    validate_generation_options,
    validate_prompt,
)


def validate_job(job):
    if job.get("operation") not in ("generate", "evaluate"):
        raise NeuronSteerError("unsupported operation")
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
        if not isinstance(job["limit"], int) or not 0 <= job["limit"] <= 10000:
            raise NeuronSteerError("--limit must be 0–10000")
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
    identity.update(prompts=prompts, model_files=files)
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
    return {"operation": "generate", "runtime": runtime.info, **result}


def generation_options(job):
    return {
        key: job[key]
        for key in ("max_tokens", "temperature", "context_size", "seed", "no_thinking")
    }


def evaluate(job, specs):
    prompts = load_prompts(pathlib.Path(job["prompts"]))
    if job["limit"]:
        if job["limit"] > len(prompts):
            raise NeuronSteerError("--limit exceeds the prompt count")
        prompts = prompts[: job["limit"]]
    output = pathlib.Path(job["output"])
    md_out = pathlib.Path(job["markdown_out"])
    if output.resolve() == md_out.resolve():
        raise NeuronSteerError("JSON and Markdown outputs must be different paths")
    if (
        output.resolve() == pathlib.Path(job["prompts"]).resolve()
        or md_out.resolve() == pathlib.Path(job["prompts"]).resolve()
    ):
        raise NeuronSteerError("outputs must not overwrite the prompt file")
    fingerprint = checkpoint_identity(job, prompts)
    records = []
    if output.exists():
        if not job["resume"]:
            raise NeuronSteerError(
                "output already exists; use --resume or choose a new output"
            )
        existing = json.loads(output.read_text())
        if existing.get("fingerprint") != fingerprint:
            raise NeuronSteerError(
                "resume settings, model files or prompts differ from the checkpoint"
            )
        records = existing.get("records")
        if not isinstance(records, list):
            raise NeuronSteerError("checkpoint records must be a list")
        seen = set()
        for row in records:
            index = row.get("index") if isinstance(row, dict) else None
            if (
                not isinstance(index, int)
                or index in seen
                or not 1 <= index <= len(prompts)
                or row.get("prompt") != prompts[index - 1]["prompt"]
            ):
                raise NeuronSteerError("invalid or duplicate checkpoint record")
            for key in ("baseline", "steered", "metrics"):
                if not isinstance(row.get(key), dict):
                    raise NeuronSteerError("incomplete checkpoint record")
            seen.add(index)
    done = {row["index"] for row in records}
    runtime = (
        LocalRuntime(job["model"], job["device"]) if len(done) < len(prompts) else None
    )
    payload = {
        "version": 1,
        "fingerprint": fingerprint,
        "model": job["model"],
        "targets": job["neurons"],
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
    temp = md_out.with_suffix(md_out.suffix + ".tmp")
    temp.write_text(markdown(payload))
    temp.replace(md_out)
    return {
        "operation": "evaluate",
        "output": str(output),
        "markdown": str(md_out),
        "summary": payload["summary"],
    }
