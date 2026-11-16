"""Generation and checkpointed paired evaluation, independent of CallAI."""

import contextlib
import copy
import fcntl
import hashlib
import math
import os
import pathlib
import sys

from .evaluation import load_prompts, markdown, metric_row, summarize
from .experiments import conditions, match_answer, normalize_experiment, trials
from .neuronpedia import normalize_references
from .runtime import LocalRuntime
from .steering import (
    NeuronSteerError,
    parse_specs,
    validate_generation_options,
    validate_prompt,
)
from .storage import atomic_text, read_json, write_json


def validate_job(job, *, require_prompt=True):
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
    baseline = job.get("baseline", False)
    if type(baseline) is not bool or baseline and job["operation"] != "generate":
        raise NeuronSteerError("baseline is a boolean option for generate only")
    if baseline and job["neurons"]:
        raise NeuronSteerError("baseline cannot be combined with neuron targets")
    specs = [] if baseline else parse_specs(job.get("neurons"))
    validate_generation_options(
        **{k: job[k] for k in ("max_tokens", "temperature", "context_size", "seed")}
    )
    if type(job.get("timeout")) not in (int, float) or not 1 <= job["timeout"] <= 86400:
        raise NeuronSteerError("--timeout must be 1–86400 seconds")
    if job.get("device") not in ("auto", "cpu", "mps", "cuda"):
        raise NeuronSteerError("unsupported device")
    if job["operation"] == "generate":
        if require_prompt:
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
    if job.get("system_role", "auto") not in ("auto", "merge", "strict") or job.get(
        "calibration", "skip-first"
    ) not in ("skip-first", "all"):
        raise NeuronSteerError("invalid system-role or calibration policy")
    if job.get("chat_template") is not None and (
        not isinstance(job["chat_template"], str)
        or not job["chat_template"]
        or len(job["chat_template"]) > 200
    ):
        raise NeuronSteerError("invalid chat template name")
    if job.get("asset_identity", "metadata") not in ("metadata", "sha256"):
        raise NeuronSteerError("asset identity must be metadata or sha256")
    if job["operation"] == "evaluate":
        normalize_experiment(job, specs)
    return specs


def checkpoint_identity(job, prompts):
    from .provenance import digest, manifest

    return digest(manifest(job, prompts))


def run_job(job):
    if isinstance(job, dict) and job.get("operation") in ("inspect", "probe"):
        from .research import run_research

        return run_research(job)
    specs = validate_job(job)
    model = pathlib.Path(job["model"]).expanduser().resolve()
    if not model.is_dir():
        raise NeuronSteerError("model must point to an existing local directory")
    job["model"] = str(model)
    if job["operation"] == "evaluate":
        job["prompts"] = str(pathlib.Path(job["prompts"]).expanduser().resolve())
        for key in ("output", "markdown_out"):
            job[key] = str(pathlib.Path(job[key]).expanduser().absolute())
        return evaluate(job, specs)
    from .provenance import manifest

    experiment = manifest(job)
    runtime = LocalRuntime(model, job["device"])
    result = runtime.generate(job["prompt"], specs, **generation_options(job))
    return {
        "operation": "generate",
        "mode": "baseline" if job.get("baseline", False) else "steered",
        "runtime": runtime.info,
        "experiment_manifest": experiment,
        "neuronpedia_references": job["references"],
        "references_are_annotations": True,
        **result,
    }


def generation_options(job):
    return {
        key: job[key]
        for key in (
            "max_tokens",
            "temperature",
            "context_size",
            "seed",
            "no_thinking",
            "system_role",
            "chat_template",
            "calibration",
        )
        if key in job
    }


def validate_output_paths(job):
    model = pathlib.Path(job["model"]).resolve()
    prompt = pathlib.Path(job["prompts"]).resolve()
    output, md_out = (pathlib.Path(job[key]) for key in ("output", "markdown_out"))
    if output.resolve() == md_out.resolve() or (
        output.exists() and md_out.exists() and output.samefile(md_out)
    ):
        raise NeuronSteerError("JSON and Markdown outputs must be different paths")
    if job["resume"] and not output.is_file():
        raise NeuronSteerError("resume requires an existing JSON checkpoint file")
    for target in (output, md_out):
        if target.exists() and not target.is_file():
            raise NeuronSteerError(
                "report paths must name files, not directories or devices"
            )
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


def validate_records(records, prompts, maximum, job=None, specs=None):
    if not isinstance(records, list):
        raise NeuronSteerError("checkpoint records must be a list")
    seen = set()
    for row in records:
        index = row.get("index") if isinstance(row, dict) else None
        if type(index) is not int or index in seen or not 1 <= index <= len(prompts):
            raise NeuronSteerError("invalid or duplicate checkpoint record")
        if any(
            row.get(key) != prompts[index - 1].get(key)
            for key in (
                "prompt",
                "category",
                "prompt_index",
                "seed",
                "dose_scale",
                "answers",
                "match",
            )
        ):
            raise NeuronSteerError("checkpoint prompt/category mismatch")
        control_results = row.get("controls", {})
        if not isinstance(control_results, dict):
            raise NeuronSteerError("invalid checkpoint controls")
        if job is not None:
            expected = set(
                conditions(
                    specs, prompts[index - 1]["dose_scale"], job.get("controls", [])
                )
            ) - {"baseline", "steered"}
            if set(control_results) != expected:
                raise NeuronSteerError("checkpoint control set mismatch")
            expected_order = list(
                conditions(
                    specs, prompts[index - 1]["dose_scale"], job.get("controls", [])
                )
            )
            if job.get("order", "alternate") == "alternate" and index % 2 == 0:
                expected_order[0], expected_order[1] = (
                    expected_order[1],
                    expected_order[0],
                )
            if row.get("execution_order") != expected_order:
                raise NeuronSteerError("invalid checkpoint execution order")
        for side, result in [
            ("baseline", row.get("baseline")),
            ("steered", row.get("steered")),
            *control_results.items(),
        ]:
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
            if "input_tokens" in result and (
                type(result["input_tokens"]) is not int or result["input_tokens"] < 0
            ):
                raise NeuronSteerError("invalid checkpoint input token count")
            if (
                "reached_token_budget" in result
                and type(result["reached_token_budget"]) is not bool
            ):
                raise NeuronSteerError("invalid checkpoint token budget flag")
            if "raw_content" in result and (
                not isinstance(result["raw_content"], str)
                or result["raw_content"].strip() != result["content"]
            ):
                raise NeuronSteerError("invalid checkpoint raw content")
            for flag in (
                "possibly_truncated",
                "ended_with_eos",
                "stop_reason_is_inferred",
            ):
                if flag in result and type(result[flag]) is not bool:
                    raise NeuronSteerError("invalid checkpoint generation flag")
            if job is not None:
                arm_map = conditions(
                    specs, prompts[index - 1]["dose_scale"], job.get("controls", [])
                )
                arm_specs, control = arm_map[side]
                options = {
                    **generation_options(job),
                    "seed": prompts[index - 1]["seed"],
                }
                if control == "random":
                    options.update(
                        control="random", control_seed=prompts[index - 1]["seed"]
                    )
                expected_execution = {
                    "prompt": prompts[index - 1]["prompt"],
                    "seed": prompts[index - 1]["seed"],
                    "dose_scale": prompts[index - 1]["dose_scale"],
                    "targets": arm_specs,
                    "control": control,
                    "condition": side,
                    "generation_options": options,
                }
                if result.get("execution") != expected_execution:
                    raise NeuronSteerError(
                        "checkpoint execution settings do not match the trial"
                    )
            if result.get("reference_match") != match_answer(
                result.get("raw_content", result["content"]), prompts[index - 1]
            ):
                raise NeuronSteerError(
                    "checkpoint reference score does not match answer key"
                )
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


def evaluation_preflight(job):
    prompts = load_prompts(pathlib.Path(job["prompts"]))
    if job["limit"]:
        if job["limit"] > len(prompts):
            raise NeuronSteerError("--limit exceeds the prompt count")
        prompts = prompts[: job["limit"]]
    validate_output_paths(job)
    trial_rows = trials(prompts, job)
    specs = parse_specs(job["neurons"])
    planned = sum(
        len(conditions(specs, row["dose_scale"], job.get("controls", [])))
        for row in trial_rows
    )
    if planned > 100000:
        raise NeuronSteerError("experiment exceeds 100000 generations")
    return prompts


def evaluate_locked(job, specs):
    prompts = evaluation_preflight(job)
    trial_rows = trials(prompts, job)
    output = pathlib.Path(job["output"])
    md_out = pathlib.Path(job["markdown_out"])
    from .provenance import digest, manifest

    experiment = manifest(job, prompts)
    fingerprint = digest(experiment)
    records = []
    if output.exists():
        if not job["resume"]:
            raise NeuronSteerError(
                "output already exists; use --resume or choose a new output"
            )
        existing = read_json(output, 128 * 1024 * 1024)
        if (
            not isinstance(existing, dict)
            or type(existing.get("version")) is not int
            or existing.get("version") != 3
        ):
            raise NeuronSteerError(
                "unsupported checkpoint format; start a new v3 report"
            )
        if existing.get("fingerprint") != fingerprint:
            raise NeuronSteerError(
                "resume settings, model files or prompts differ from the checkpoint"
            )
        if existing.get("experiment_manifest") != experiment:
            raise NeuronSteerError(
                "checkpoint manifest does not match the current experiment"
            )
        records = existing.get("records")
        validate_records(records, trial_rows, job["max_tokens"], job, specs)
        if not isinstance(existing.get("runtime"), dict):
            raise NeuronSteerError("checkpoint runtime must be an object")
    done = {row["index"] for row in records}
    runtime = (
        LocalRuntime(job["model"], job["device"])
        if len(done) < len(trial_rows)
        else None
    )
    if records and runtime is not None and existing["runtime"] != runtime.info:
        raise NeuronSteerError("resolved runtime differs from the checkpoint")
    payload = {
        "version": 3,
        "fingerprint": fingerprint,
        "experiment_manifest": experiment,
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
    for row in trial_rows:
        if row["index"] in done:
            continue
        print(
            f"[{row['index']}/{len(trial_rows)}] prompt={row['prompt_index']} seed={row['seed']} dose_scale={row['dose_scale']}",
            file=sys.stderr,
            flush=True,
        )
        arms = conditions(specs, row["dose_scale"], job.get("controls", []))
        names = list(arms)
        if job.get("order", "alternate") == "alternate" and row["index"] % 2 == 0:
            names[0], names[1] = names[1], names[0]
        outputs = {}
        for name in names:
            arm_specs, control = arms[name]
            options = {**generation_options(job), "seed": row["seed"]}
            if control == "random":
                options.update(control="random", control_seed=row["seed"])
            outputs[name] = copy.deepcopy(
                runtime.generate(row["prompt"], arm_specs, **options)
            )
            outputs[name]["execution"] = {
                "prompt": row["prompt"],
                "seed": row["seed"],
                "dose_scale": row["dose_scale"],
                "targets": arm_specs,
                "control": control,
                "condition": name,
                "generation_options": options,
            }
            outputs[name]["reference_match"] = match_answer(
                outputs[name].get("raw_content", outputs[name]["content"]), row
            )
        record = {
            **row,
            "execution_order": names,
            "baseline": outputs.pop("baseline"),
            "steered": outputs.pop("steered"),
            "controls": outputs,
        }
        record["metrics"] = metric_row(
            record["baseline"]["content"], record["steered"]["content"]
        )
        records.append(record)
        records.sort(key=lambda r: r["index"])
        payload["summary"] = summarize(records, uncertainty=False)
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
