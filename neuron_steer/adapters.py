"""Bounded supervised LoRA training and verified export to fresh local artifacts."""

import contextlib
import gc
import importlib.metadata
import math
import os
import pathlib
import random
import re
import shutil
import sys
import tempfile

from .evaluation import load_prompts, metric_row
from .experiments import match_answer
from .provenance import digest, hash_file, manifest
from .runtime import LocalRuntime, prepare_chat_prompt, snapshot_inputs
from .steering import NeuronSteerError, validate_prompt
from .storage import read_json, write_json

ADAPTER_MANIFEST = "neuron_steer_adapter.json"
EXPORT_MANIFEST = "neuron_steer_export.json"


def normalized_prompt(text):
    return " ".join(text.split()).casefold()


def validate_example_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2048:
        raise NeuronSteerError(
            "training data must contain 1–2048 prompt/response objects"
        )
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"prompt", "response"}:
            raise NeuronSteerError(
                "each training example requires exactly prompt and response"
            )
        validate_prompt(row["prompt"])
        validate_prompt(row["response"])
        key = normalized_prompt(row["prompt"])
        if key in seen:
            raise NeuronSteerError("duplicate normalized prompt in dataset")
        seen.add(key)
    return rows


def load_examples(path):
    return validate_example_rows(read_json(path))


def fresh_output(job):
    target = pathlib.Path(job["output_dir"])
    if target.exists() or target.is_symlink():
        raise NeuronSteerError(
            "output directory must be a fresh path; overwriting is not supported"
        )
    resolved = target.resolve()
    for name in ("model", "adapter", "train_data", "validation_data", "prompts"):
        if not job.get(name):
            continue
        source = pathlib.Path(job[name]).resolve()
        inside = resolved.is_relative_to(source)
        # samefile also catches case aliases on case-insensitive filesystems.
        if source.is_dir():
            inside = inside or any(
                parent.exists() and parent.samefile(source)
                for parent in resolved.parents
            )
        if inside:
            raise NeuronSteerError(
                "output must be outside model, adapter and input paths"
            )
    return target


def bounded_number(job, key, default, low, high, integer=False):
    value = job.get(key, default)
    allowed = type(value) is int if integer else type(value) in (int, float)
    if not allowed or not low <= value <= high or not math.isfinite(value):
        raise NeuronSteerError(
            f"{key} must be {'an integer ' if integer else ''}within {low}–{high}"
        )
    job[key] = value


def read_adapter(path):
    path = pathlib.Path(path)
    if not path.is_dir() or path.is_symlink():
        raise NeuronSteerError("adapter must be a local managed adapter directory")
    if any(
        (path / name).is_symlink()
        for name in (
            ADAPTER_MANIFEST,
            "adapter_config.json",
            "adapter_model.safetensors",
        )
    ):
        raise NeuronSteerError("managed adapter files must not be symlinks")
    data = read_json(path / ADAPTER_MANIFEST, 64 * 1024 * 1024)
    if (
        not isinstance(data, dict)
        or type(data.get("artifact_version")) is not int
        or data["artifact_version"] != 1
        or data.get("kind") != "lora"
    ):
        raise NeuronSteerError("unsupported adapter manifest")
    if (
        not isinstance(data.get("base_identity"), dict)
        or not isinstance(data.get("assets"), list)
        or not data["assets"]
    ):
        raise NeuronSteerError("adapter manifest is missing identity or assets")
    base = data["base_identity"]
    if not isinstance(base.get("assets"), list) or base.get("sha256") != digest(
        base["assets"]
    ):
        raise NeuronSteerError("invalid base identity fingerprint")
    for key in ("train_examples", "validation_examples"):
        validate_example_rows(data.get(key))
    if {normalized_prompt(row["prompt"]) for row in data["train_examples"]} & {
        normalized_prompt(row["prompt"]) for row in data["validation_examples"]
    }:
        raise NeuronSteerError("adapter datasets overlap")
    policy = data.get("policy")
    if (
        not isinstance(policy, dict)
        or type(policy.get("no_thinking")) is not bool
        or policy.get("system_role") not in ("auto", "merge", "strict")
        or type(policy.get("max_seq_length")) is not int
        or not 8 <= policy["max_seq_length"] <= 2048
    ):
        raise NeuronSteerError("invalid saved training policy")
    if policy.get("chat_template") is not None and (
        not isinstance(policy["chat_template"], str)
        or not 1 <= len(policy["chat_template"]) <= 200
    ):
        raise NeuronSteerError("invalid saved template name")
    config = read_json(path / "adapter_config.json")
    if (
        not isinstance(config, dict)
        or config.get("peft_type") != "LORA"
        or config.get("task_type") != "CAUSAL_LM"
    ):
        raise NeuronSteerError("only managed causal-LM LoRA adapters are supported")
    if not (path / "adapter_model.safetensors").is_file() or any(path.rglob("*.bin")):
        raise NeuronSteerError(
            "adapter requires safetensors weights; pickle/bin files are refused"
        )
    for entry in data["assets"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise NeuronSteerError("invalid adapter asset record")
        relative = pathlib.PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise NeuronSteerError("adapter asset path escapes its directory")
        asset = path / entry["path"]
        if (
            asset.is_symlink()
            or not asset.is_file()
            or not asset.resolve().is_relative_to(path.resolve())
        ):
            raise NeuronSteerError("adapter asset is missing or is an unsafe symlink")
    return data


def validate_adapter_job(job, require_prompt=True):
    provided_policy = {
        key for key in ("system_role", "chat_template", "no_thinking") if key in job
    }
    if not isinstance(job, dict) or job.get("action") not in (
        "train",
        "evaluate",
        "generate",
        "merge",
    ):
        raise NeuronSteerError("unsupported adapter action")
    for key in ("model",):
        if not isinstance(job.get(key), str) or not job[key].strip():
            raise NeuronSteerError("adapter job requires an explicit local model")
        job[key] = str(pathlib.Path(job[key]).expanduser().resolve())
    bounded_number(job, "timeout", 900, 1, 86400)
    bounded_number(job, "seed", 0, -100000000, 100000000, True)
    bounded_number(job, "max_seq_length", 512, 8, 2048, True)
    if job.get("device", "auto") not in ("auto", "cpu", "mps", "cuda"):
        raise NeuronSteerError("unsupported device")
    job.setdefault("device", "auto")
    job.setdefault("system_role", "auto")
    job.setdefault("chat_template", None)
    job.setdefault("no_thinking", True)
    if (
        job["system_role"] not in ("auto", "merge", "strict")
        or type(job["no_thinking"]) is not bool
    ):
        raise NeuronSteerError("invalid prompt policy")
    if job["chat_template"] is not None and (
        not isinstance(job["chat_template"], str)
        or not 1 <= len(job["chat_template"]) <= 200
    ):
        raise NeuronSteerError("invalid named template")
    if job["action"] != "generate":
        if not isinstance(job.get("output_dir"), str) or not job["output_dir"].strip():
            raise NeuronSteerError("output_dir is required")
        job["output_dir"] = str(pathlib.Path(job["output_dir"]).expanduser().absolute())
    if job["action"] == "train":
        for key in ("train_data", "validation_data"):
            if not isinstance(job.get(key), str) or not job[key]:
                raise NeuronSteerError("train_data and validation_data are required")
            job[key] = str(pathlib.Path(job[key]).expanduser().resolve())
        train, validation = (
            load_examples(job[key]) for key in ("train_data", "validation_data")
        )
        if {normalized_prompt(r["prompt"]) for r in train} & {
            normalized_prompt(r["prompt"]) for r in validation
        }:
            raise NeuronSteerError("training and validation prompts must be disjoint")
        targets = job.get("target_modules")
        if (
            not isinstance(targets, list)
            or not 1 <= len(targets) <= 16
            or any(
                not isinstance(t, str)
                or len(t) > 200
                or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", t)
                for t in targets
            )
            or len(set(targets)) != len(targets)
        ):
            raise NeuronSteerError(
                "select 1–16 unique explicit target-module names or suffixes"
            )
        for key, default, low, high, integer in (
            ("rank", 8, 1, 128, True),
            ("alpha", 16, 1, 256, True),
            ("steps", 100, 1, 5000, True),
            ("learning_rate", 0.0001, 0.0000001, 0.01, False),
            ("max_validation_regression", 0, 0, 10, False),
        ):
            bounded_number(job, key, default, low, high, integer)
        if type(job.get("gradient_checkpointing", False)) is not bool:
            raise NeuronSteerError("gradient_checkpointing must be boolean")
        selected = {
            "train_examples": len(train),
            "validation_examples": len(validation),
        }
    else:
        if not isinstance(job.get("adapter"), str) or not job["adapter"]:
            raise NeuronSteerError("an explicit managed adapter path is required")
        job["adapter"] = str(pathlib.Path(job["adapter"]).expanduser().absolute())
        data = read_adapter(job["adapter"])
        for key in ("system_role", "chat_template", "no_thinking"):
            if key not in provided_policy:
                job[key] = data["policy"].get(key)
        if job["action"] == "generate":
            if require_prompt:
                validate_prompt(job.get("prompt"))
            selected = {}
        elif job["action"] == "evaluate":
            if not isinstance(job.get("prompts"), str) or not job["prompts"]:
                raise NeuronSteerError("evaluation requires a prompt file")
            job["prompts"] = str(pathlib.Path(job["prompts"]).expanduser().resolve())
            bounded_number(job, "limit", 20, 1, 128, True)
            rows = load_prompts(pathlib.Path(job["prompts"]))[: job["limit"]]
            excluded = {
                normalized_prompt(r["prompt"])
                for name in ("train_examples", "validation_examples")
                for r in data.get(name, [])
            }
            keys = [normalized_prompt(r["prompt"]) for r in rows]
            if len(set(keys)) != len(keys) or set(keys) & excluded:
                raise NeuronSteerError(
                    "adapter evaluation needs unique held-out prompts, separate from training and validation"
                )
            selected = {"prompts": len(rows), "generations": 2 * len(rows)}
        else:
            bounded_number(job, "tolerance", 0.05, 0.000001, 0.1)
            selected = {
                "reload_verification_examples": min(
                    4, len(data.get("validation_examples", []))
                )
            }
            if not selected["reload_verification_examples"]:
                raise NeuronSteerError(
                    "adapter has no validation examples for export verification"
                )
    if job["action"] in ("generate", "evaluate"):
        bounded_number(job, "max_tokens", 64, 1, 32768, True)
        bounded_number(job, "temperature", 0, 0, 2)
        bounded_number(job, "context_size", 0, 0, 1048576, True)
    if job["action"] != "generate":
        fresh_output(job)
    return selected


def require_peft():
    os.environ.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1"
    )
    if sys.version_info < (3, 11):
        raise NeuronSteerError(
            "adapter operations require Python 3.11+; dry-runs work without training dependencies"
        )
    try:
        import peft
    except ImportError as exc:
        raise NeuronSteerError(
            "install optional training dependencies: pip install 'neuron-steer[training]'"
        ) from exc
    return peft


def experiment_identity(job):
    result = manifest(
        {
            **job,
            "operation": "adapter",
            "neurons": [],
            "max_tokens": job.get("max_tokens", 0),
            "temperature": job.get("temperature", 0),
            "context_size": job.get("context_size", 0),
            "asset_identity": "sha256",
        }
    )
    result["adapter_job"] = dict(job)
    result["data_files_sha256"] = {
        key: hash_file(pathlib.Path(job[key]))
        for key in ("train_data", "validation_data", "prompts")
        if job.get(key)
    }
    return result


def base_identity(experiment):
    assets = [
        {k: entry[k] for k in ("path", "bytes", "sha256")}
        for entry in experiment["model_assets"]
    ]
    if not any(entry["path"].endswith(".safetensors") for entry in assets):
        raise NeuronSteerError("base model requires existing safetensors weights")
    return {"assets": assets, "sha256": digest(assets)}


def artifact_assets(directory):
    paths = sorted(directory.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise NeuronSteerError("adapter/export artifacts must not contain symlinks")
    return [
        {
            "path": path.relative_to(directory).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hash_file(path),
        }
        for path in paths
        if path.is_file() and path.name not in (ADAPTER_MANIFEST, EXPORT_MANIFEST)
    ]


@contextlib.contextmanager
def publish(job):
    from .jobs import checkpoint_lock

    output = fresh_output(job)
    with checkpoint_lock(output):
        fresh_output(job)
        staging = pathlib.Path(
            tempfile.mkdtemp(prefix="." + output.name + ".partial-", dir=output.parent)
        )
        try:
            yield staging
            fresh_output(job)
            os.rename(staging, output)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def encode_example(runtime, row, policy, max_length):
    torch = runtime.torch
    rendered, formatting = prepare_chat_prompt(
        runtime.tokenizer,
        row["prompt"],
        policy.get("no_thinking", True),
        policy.get("system_role", "auto"),
        policy.get("chat_template"),
    )
    encoded = runtime.tokenizer(
        rendered,
        return_tensors="pt",
        add_special_tokens=formatting["add_special_tokens"],
    )
    if set(encoded) - {"input_ids", "attention_mask", "token_type_ids"}:
        raise NeuronSteerError("training tokenizer returned unsupported model inputs")
    prefix = int(encoded["input_ids"].shape[-1])
    if prefix < 1:
        raise NeuronSteerError("training requires a nonempty formatted prompt")
    answer = runtime.tokenizer(row["response"], add_special_tokens=False)["input_ids"]
    eos = runtime.tokenizer.eos_token_id
    if type(eos) is not int or not answer:
        raise NeuronSteerError(
            "training needs response tokens and a tokenizer EOS token"
        )
    answer = answer if answer[-1] == eos else [*answer, eos]
    total = prefix + len(answer)
    supported = getattr(runtime.model.config, "max_position_embeddings", 0)
    if total > max_length or (
        type(supported) is int and supported > 0 and total > supported
    ):
        raise NeuronSteerError(
            "prompt plus response exceeds training token/context bound; truncation is refused"
        )
    tensors = {}
    for key, value in encoded.items():
        if value.ndim != 2 or value.shape[0] != 1 or value.shape[1] != prefix:
            raise NeuronSteerError("unexpected training tokenizer tensor shape")
        tail = (
            torch.tensor([answer], dtype=value.dtype)
            if key == "input_ids"
            else torch.ones((1, len(answer)), dtype=value.dtype)
            if key == "attention_mask"
            else value[:, -1:].expand(1, len(answer))
        )
        tensors[key] = torch.cat([value, tail], dim=1)
    labels = torch.tensor([[-100] * prefix + answer], dtype=torch.long)
    return {
        "row": row,
        "rendered_prompt": rendered,
        "formatting": formatting,
        "model_inputs": snapshot_inputs(tensors),
        "labels": labels.tolist(),
        "supervised_tokens": len(answer),
    }


def tensor_example(runtime, example):
    torch = runtime.torch
    values = {
        key: torch.tensor(
            entry["values"],
            dtype=getattr(torch, entry["dtype"].removeprefix("torch.")),
            device=runtime.device,
        )
        for key, entry in example["model_inputs"].items()
    }
    return values, torch.tensor(
        example["labels"], dtype=torch.long, device=runtime.device
    )


def model_forward(runtime, inputs, labels=None):
    # Some Transformers versions consult the shared config in nested forwards,
    # even when return_dict is explicitly passed to the outer causal model.
    runtime.model.config.return_dict = True
    runtime.info["forward_return_dict"] = True
    options = {"use_cache": False, "return_dict": True}
    if labels is not None:
        options["labels"] = labels
    return runtime.model(**inputs, **options)


def supervised_loss(runtime, examples):
    runtime.model.eval()
    total = 0.0
    count = 0
    with runtime.torch.inference_mode():
        for example in examples:
            inputs, labels = tensor_example(runtime, example)
            loss = float(model_forward(runtime, inputs, labels).loss.item())
            if not math.isfinite(loss):
                raise NeuronSteerError("non-finite supervised loss")
            total += loss * example["supervised_tokens"]
            count += example["supervised_tokens"]
    return total / count


def select_modules(runtime, targets):
    modules = dict(runtime.model.named_modules())
    selected = {
        name: module
        for name, module in modules.items()
        if any(name == t or name.endswith("." + t) for t in targets)
    }
    if any(
        not any(name == t or name.endswith("." + t) for name in selected)
        for t in targets
    ):
        raise NeuronSteerError("a requested target module does not exist in this model")
    for name, module in selected.items():
        weight = getattr(module, "weight", None)
        if (
            not (
                isinstance(module, runtime.torch.nn.Linear)
                or type(module).__name__ == "Conv1D"
            )
            or getattr(weight, "ndim", 0) != 2
            or getattr(weight, "is_quantized", False)
            or not weight.is_floating_point()
        ):
            raise NeuronSteerError(
                "LoRA targets must be dense floating-point Linear/Conv1D modules"
            )
        if any(
            other is not module and getattr(other, "weight", None) is weight
            for other in modules.values()
        ):
            raise NeuronSteerError(
                f"target {name} has shared/tied weights; merging is unsupported"
            )
    return sorted(selected)


def verify_assets(path, data):
    actual = artifact_assets(path)
    if actual != data["assets"]:
        raise NeuronSteerError("adapter files changed or do not match their manifest")


def load_managed(runtime, job, experiment):
    peft = require_peft()
    runtime.model.config.return_dict = True
    runtime.info["forward_return_dict"] = True
    path = pathlib.Path(job["adapter"])
    data = read_adapter(path)
    verify_assets(path, data)
    if data["base_identity"] != base_identity(experiment):
        raise NeuronSteerError(
            "adapter was trained against different base weights/tokenizer assets"
        )
    saved_encoding = data.get("encoded_validation")
    if not isinstance(saved_encoding, list) or len(saved_encoding) != len(
        data["validation_examples"]
    ):
        raise NeuronSteerError("adapter is missing its saved validation tokenization")
    for row, saved in zip(data["validation_examples"], saved_encoding, strict=True):
        current = encode_example(
            runtime, row, data["policy"], data["policy"]["max_seq_length"]
        )
        if (
            not isinstance(saved, dict)
            or current["model_inputs"] != saved.get("model_inputs")
            or current["labels"] != saved.get("labels")
        ):
            raise NeuronSteerError(
                "saved training tokenization differs; use a compatible tokenizer/runtime/system policy"
            )
    runtime.model = peft.PeftModel.from_pretrained(
        runtime.model, str(path), is_trainable=False, local_files_only=True
    )
    runtime.model.eval()
    runtime.info = {
        **runtime.info,
        "base_model_class": runtime.info["model_class"],
        "model_class": type(runtime.model).__module__
        + "."
        + type(runtime.model).__qualname__,
        "peft": importlib.metadata.version("peft"),
        "adapter": str(path),
        "adapter_identity": digest(data["assets"]),
    }
    return data


def train_adapter(job, experiment):
    peft = require_peft()
    runtime = LocalRuntime(job["model"], job["device"])
    torch = runtime.torch
    if getattr(runtime.model, "is_quantized", False):
        raise NeuronSteerError("quantized adapter training/merging is not supported")
    train_rows = load_examples(job["train_data"])
    validation_rows = load_examples(job["validation_data"])
    encoded = {"train": [], "validation": []}
    total_tokens = 0
    for name, rows in (("train", train_rows), ("validation", validation_rows)):
        for row in rows:
            example = encode_example(runtime, row, job, job["max_seq_length"])
            total_tokens += len(example["labels"][0])
            if total_tokens > 200000:
                raise NeuronSteerError(
                    "training data exceeds the 200000 encoded-token budget"
                )
            encoded[name].append(example)
    train, validation = encoded["train"], encoded["validation"]
    matched = select_modules(runtime, job["target_modules"])
    before = supervised_loss(runtime, validation)
    torch.manual_seed(job["seed"])
    config = peft.LoraConfig(
        task_type=peft.TaskType.CAUSAL_LM,
        r=job["rank"],
        lora_alpha=job["alpha"],
        lora_dropout=0.0,
        bias="none",
        target_modules=job["target_modules"],
    )
    runtime.model = peft.get_peft_model(runtime.model, config)
    if job.get("gradient_checkpointing"):
        runtime.model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )
    trainable = [
        (name, p) for name, p in runtime.model.named_parameters() if p.requires_grad
    ]
    if any(".lora_A." not in name and ".lora_B." not in name for name, _ in trainable):
        raise NeuronSteerError(
            "unexpected trainable base parameters; only LoRA A/B updates are allowed"
        )
    parameters = [p for _, p in trainable]
    if not parameters:
        raise NeuronSteerError("no trainable adapter parameters")
    optimizer = torch.optim.AdamW(parameters, lr=job["learning_rate"], weight_decay=0.0)
    rng = random.Random(job["seed"])
    order = list(range(len(train)))
    trace = []
    runtime.model.train()
    for step in range(job["steps"]):
        if step % len(order) == 0:
            rng.shuffle(order)
        index = order[step % len(order)]
        example = train[index]
        inputs, labels = tensor_example(runtime, example)
        optimizer.zero_grad(set_to_none=True)
        loss = model_forward(runtime, inputs, labels).loss
        if not bool(torch.isfinite(loss).item()):
            raise NeuronSteerError("non-finite training loss")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(parameters, 1.0, error_if_nonfinite=True)
        optimizer.step()
        trace.append(
            {
                "step": step + 1,
                "example": index + 1,
                "loss": float(loss.detach().item()),
            }
        )
        if step == 0 or (step + 1) % 10 == 0 or step + 1 == job["steps"]:
            print(
                f"adapter step {step + 1}/{job['steps']} loss={trace[-1]['loss']:.6f}",
                file=sys.stderr,
                flush=True,
            )
    for p in parameters:
        if not bool(torch.isfinite(p).all().item()):
            raise NeuronSteerError("non-finite adapter parameters")
    after = supervised_loss(runtime, validation)
    if after > before + job["max_validation_regression"]:
        raise NeuronSteerError(
            f"validation loss regression: {before:.6f} -> {after:.6f}; no adapter published"
        )
    policy = {
        key: job[key]
        for key in ("no_thinking", "system_role", "chat_template", "max_seq_length")
    }
    templates = {}
    for example in train + validation:
        template = example["formatting"].pop("template")
        key = digest(template)
        templates[key] = template
        example["formatting"]["template_sha256"] = key
    data = {
        "artifact_version": 1,
        "kind": "lora",
        "base_identity": base_identity(experiment),
        "experiment_manifest": experiment,
        "training_settings": job,
        "policy": policy,
        "matched_modules": matched,
        "train_examples": train_rows,
        "validation_examples": validation_rows,
        "encoded_train": train,
        "encoded_validation": validation,
        "chat_templates": templates,
        "validation_loss": {"base": before, "adapter": after, "delta": after - before},
        "trainable_parameters": sum(p.numel() for p in parameters),
        "trainable_parameter_dtypes": {
            str(dtype): sum(p.numel() for p in parameters if p.dtype == dtype)
            for dtype in {p.dtype for p in parameters}
        },
        "trace": trace,
        "peft": importlib.metadata.version("peft"),
        "runtime": runtime.info,
        "behavior_improvement_verified": False,
    }
    with publish(job) as staging:
        runtime.model.save_pretrained(staging, safe_serialization=True)
        data["assets"] = artifact_assets(staging)
        # Release the training model before loading a fresh base, avoiding two
        # simultaneous full model copies when verifying the saved adapter.
        device = runtime.device
        runtime.model = None
        del optimizer, parameters, trainable, loss, inputs, labels, p
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()
        elif device == "mps" and hasattr(torch.mps, "empty_cache"):
            torch.mps.empty_cache()
        fresh = LocalRuntime(job["model"], device)
        fresh.model = peft.PeftModel.from_pretrained(
            fresh.model, str(staging), is_trainable=False, local_files_only=True
        )
        reload_loss = supervised_loss(fresh, validation)
        reload_tolerance = 0.0001 if fresh.model.dtype == torch.float32 else 0.05
        if abs(reload_loss - after) > reload_tolerance:
            raise NeuronSteerError("saved adapter fails fresh-reload loss verification")
        data["reload_verification"] = {
            "validation_loss": reload_loss,
            "max_loss_error": abs(reload_loss - after),
            "tolerance": reload_tolerance,
        }
        write_json(staging / ADAPTER_MANIFEST, data, max_bytes=64 * 1024 * 1024)
        read_adapter(staging)
        verify_assets(staging, data)
    return {
        "operation": "adapter",
        "action": "train",
        "output_dir": job["output_dir"],
        "validation_loss": data["validation_loss"],
        "trainable_parameters": data["trainable_parameters"],
        "base_files_modified": False,
        "behavior_improvement_verified": False,
    }


def generation_options(job):
    return {
        key: job[key]
        for key in (
            "max_tokens",
            "temperature",
            "seed",
            "context_size",
            "no_thinking",
            "system_role",
            "chat_template",
        )
    }


def evaluate_adapter(runtime, job, data, experiment):
    rows = load_prompts(pathlib.Path(job["prompts"]))[: job["limit"]]
    records = []
    for index, row in enumerate(rows, 1):
        names = ["baseline", "adapter"] if index % 2 else ["adapter", "baseline"]
        outputs = {}
        for name in names:
            context = (
                runtime.model.disable_adapter()
                if name == "baseline"
                else contextlib.nullcontext()
            )
            with context:
                outputs[name] = runtime.generate(
                    row["prompt"], [], **generation_options(job)
                )
            outputs[name]["adapter_enabled"] = name == "adapter"
            outputs[name]["reference_match"] = match_answer(
                outputs[name]["raw_content"], row
            )
        records.append(
            {
                **row,
                "order": names,
                **outputs,
                "metrics": metric_row(
                    outputs["baseline"]["content"], outputs["adapter"]["content"]
                ),
            }
        )
    complete = [
        r
        for r in records
        if not r["baseline"]["possibly_truncated"]
        and not r["adapter"]["possibly_truncated"]
    ]
    scored = [
        r
        for r in complete
        if type(r["baseline"]["reference_match"]) is bool
        and type(r["adapter"]["reference_match"]) is bool
    ]
    accuracy = (
        {
            side: sum(r[side]["reference_match"] for r in scored) / len(scored)
            for side in ("baseline", "adapter")
        }
        if scored
        else None
    )
    summary = {
        "prompts": len(records),
        "generations": 2 * len(records),
        "complete_pairs": len(complete),
        "possibly_truncated_pairs": len(records) - len(complete),
        "scored_pairs": len(scored),
        "reference_accuracy": accuracy,
        "truthfulness_verified": False,
    }
    if accuracy:
        summary["reference_accuracy_delta"] = accuracy["adapter"] - accuracy["baseline"]
    payload = {
        "operation": "adapter",
        "action": "evaluate",
        "experiment_manifest": experiment,
        "adapter_identity": digest(data["assets"]),
        "runtime": runtime.info,
        "summary": summary,
        "records": records,
    }
    with publish(job) as staging:
        write_json(staging / "evaluation.json", payload)
    return {
        "operation": "adapter",
        "action": "evaluate",
        "output": str(pathlib.Path(job["output_dir"]) / "evaluation.json"),
        "summary": summary,
    }


def answer_log_probs(runtime, examples):
    torch = runtime.torch
    result = []
    runtime.model.eval()
    with torch.inference_mode():
        for example in examples:
            inputs, labels = tensor_example(runtime, example)
            target = labels[0, 1:]
            mask = target != -100
            logits = model_forward(runtime, inputs).logits[0, :-1][mask].float()
            values = (
                torch.log_softmax(logits, dim=-1)
                .gather(-1, target[mask, None])
                .squeeze(-1)
            )
            if not bool(torch.isfinite(values).all().item()):
                raise NeuronSteerError("non-finite export verification scores")
            result.append(values.cpu().tolist())
    return result


def merge_adapter(runtime, job, data, experiment):
    torch = runtime.torch
    examples = [
        encode_example(runtime, row, data["policy"], data["policy"]["max_seq_length"])
        for row in data["validation_examples"][:4]
    ]
    expected = answer_log_probs(runtime, examples)
    info = runtime.info
    with publish(job) as staging:
        merged = runtime.model.merge_and_unload(safe_merge=True)
        merged.eval()
        merged.save_pretrained(staging, safe_serialization=True)
        runtime.tokenizer.save_pretrained(staging)
        del merged
        runtime.model = None
        gc.collect()
        if runtime.device == "cuda":
            torch.cuda.empty_cache()
        elif runtime.device == "mps" and hasattr(torch.mps, "empty_cache"):
            torch.mps.empty_cache()
        reloaded = LocalRuntime(staging, runtime.device)
        if getattr(reloaded.model, "peft_config", None):
            raise NeuronSteerError("export reload unexpectedly requires an adapter")
        fresh = [
            encode_example(
                reloaded, row, data["policy"], data["policy"]["max_seq_length"]
            )
            for row in data["validation_examples"][:4]
        ]
        if [e["model_inputs"] for e in fresh] != [e["model_inputs"] for e in examples]:
            raise NeuronSteerError("export tokenizer replay differs")
        actual = answer_log_probs(reloaded, fresh)
        error = max(
            abs(a - b)
            for old, new in zip(expected, actual, strict=True)
            for a, b in zip(old, new, strict=True)
        )
        if error > job["tolerance"]:
            raise NeuronSteerError(
                f"export reload mismatch {error:.6f} exceeds tolerance {job['tolerance']}; no model published"
            )
        report = {
            "artifact_version": 1,
            "kind": "merged-model",
            "experiment_manifest": experiment,
            "adapter_identity": digest(data["assets"]),
            "base_identity": data["base_identity"],
            "source_runtime": info,
            "reload_runtime": reloaded.info,
            "verification": {
                "examples": len(examples),
                "max_answer_log_probability_error": error,
                "tolerance": job["tolerance"],
                "adapter_required": False,
                "steering_hooks_required": False,
                "behavior_improvement_verified": False,
            },
            "assets": artifact_assets(staging),
        }
        write_json(staging / EXPORT_MANIFEST, report)
    return {
        "operation": "adapter",
        "action": "merge",
        "output_dir": job["output_dir"],
        "verification": report["verification"],
        "base_files_modified": False,
    }


def run_adapter(job):
    validate_adapter_job(job)
    require_peft()
    experiment = experiment_identity(job)
    if job["action"] == "train":
        return train_adapter(job, experiment)
    saved = read_adapter(job["adapter"])
    verify_assets(pathlib.Path(job["adapter"]), saved)
    if saved["base_identity"] != base_identity(experiment):
        raise NeuronSteerError(
            "adapter was trained against different base weights/tokenizer assets"
        )
    runtime = LocalRuntime(job["model"], job["device"])
    data = load_managed(runtime, job, experiment)
    if job["action"] == "generate":
        return {
            "operation": "adapter",
            "action": "generate",
            "mode": "trained-adapter",
            "runtime": runtime.info,
            "experiment_manifest": experiment,
            **runtime.generate(job["prompt"], [], **generation_options(job)),
        }
    if job["action"] == "evaluate":
        return evaluate_adapter(runtime, job, data, experiment)
    return merge_adapter(runtime, job, data, experiment)
