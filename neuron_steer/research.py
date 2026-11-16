"""Local capability inspection and bounded, observational neuron probing."""

import math
import pathlib

from .evaluation import load_prompts
from .neuronpedia import normalize_references
from .provenance import digest, manifest, source_identity
from .runtime import LocalRuntime, json_digest, prepare_chat_prompt
from .steering import (
    NeuronSteerError,
    projection_info,
    transformer_blocks,
    transformer_down_proj,
)
from .storage import read_json


def validate_research_job(job):
    if not isinstance(job, dict) or job.get("operation") not in ("inspect", "probe"):
        raise NeuronSteerError("invalid research operation")
    if not isinstance(job.get("model"), str) or not job["model"].strip():
        raise NeuronSteerError("model must name a local directory")
    job["model"] = str(pathlib.Path(job["model"]).expanduser().resolve())
    if job.get("device", "auto") not in ("auto", "cpu", "mps", "cuda"):
        raise NeuronSteerError("unsupported device")
    timeout = job.get("timeout", 900)
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 1 <= timeout <= 86400
    ):
        raise NeuronSteerError("timeout must be 1–86400 seconds")
    if job["operation"] == "inspect":
        if type(job.get("load", False)) is not bool:
            raise NeuronSteerError("load must be boolean")
        return None
    layers = job.get("layers")
    if (
        not isinstance(layers, list)
        or not 1 <= len(layers) <= 8
        or any(type(i) is not int or not 0 <= i <= 255 for i in layers)
        or len(set(layers)) != len(layers)
    ):
        raise NeuronSteerError("select 1–8 unique layers within 0–255")
    for key, default, maximum in (
        ("top_k", 16, 64),
        ("limit", 64, 64),
        ("max_input_tokens", 512, 4096),
    ):
        value = job.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise NeuronSteerError(f"{key} must be 1–{maximum}")
        job[key] = value
    job.setdefault("positions", "last")
    if job.get("positions", "last") not in ("all", "last", "skip-first"):
        raise NeuronSteerError("unknown probe position policy")
    if (
        job.get("system_role", "auto") not in ("auto", "merge", "strict")
        or type(job.get("no_thinking", True)) is not bool
    ):
        raise NeuronSteerError("invalid prompt policy")
    name = job.get("chat_template")
    if name is not None and (not isinstance(name, str) or not name or len(name) > 200):
        raise NeuronSteerError("invalid chat template name")
    if job.get("asset_identity", "metadata") not in ("metadata", "sha256"):
        raise NeuronSteerError("invalid asset identity mode")
    job["references"] = normalize_references(job.get("references", []))
    positive, negative = job.get("positive"), job.get("negative")
    if (positive is None) != (negative is None) or (
        positive is not None
        and (
            not isinstance(positive, str)
            or not isinstance(negative, str)
            or not positive.strip()
            or not negative.strip()
            or positive == negative
        )
    ):
        raise NeuronSteerError(
            "contrast requires two different positive/negative categories"
        )
    if not isinstance(job.get("prompts"), str) or not job["prompts"]:
        raise NeuronSteerError("probe requires an explicit prompt file")
    job["prompts"] = str(pathlib.Path(job["prompts"]).expanduser().resolve())
    rows = load_prompts(pathlib.Path(job["prompts"]))[: job["limit"]]
    if positive is not None:
        rows = [row for row in rows if row["category"] in (positive, negative)]
        if {row["category"] for row in rows} != {positive, negative}:
            raise NeuronSteerError(
                "both contrast categories must occur within the selected prompt limit"
            )
    return rows


def inspect_model(job):
    path = pathlib.Path(job["model"])
    if not path.is_dir():
        raise NeuronSteerError("model must be an existing local directory")
    config = read_json(path / "config.json", 1024 * 1024)
    if not isinstance(config, dict):
        raise NeuronSteerError("config.json must be an object")
    text_config = config.get("text_config", config)
    if not isinstance(text_config, dict):
        raise NeuronSteerError("text_config must be an object")
    tokenizer = (
        read_json(path / "tokenizer_config.json", 1024 * 1024)
        if (path / "tokenizer_config.json").is_file()
        else {}
    )
    if not isinstance(tokenizer, dict):
        raise NeuronSteerError("tokenizer_config.json must be an object")
    template = tokenizer.get("chat_template")
    # Current tokenizer configs can serialize named templates as name/template records.
    names = (
        list(template)
        if isinstance(template, dict)
        else [v.get("name") for v in template if isinstance(v, dict)]
        if isinstance(template, list)
        else ["default"]
        if isinstance(template, str)
        else []
    )
    if (path / "chat_template.jinja").is_file():
        names.append("default")
    names.extend(
        file.stem
        for file in (path / "chat_templates").glob("*.jinja")
        if file.is_file()
    )
    names = sorted({name for name in names if isinstance(name, str)})
    if len(names) > 256:
        raise NeuronSteerError("too many named chat templates")
    claims = {**config, **text_config}
    reply = {
        "operation": "inspect",
        "model": str(path),
        "model_loaded": False,
        "config_claims": {
            k: claims[k]
            for k in (
                "model_type",
                "architectures",
                "hidden_size",
                "intermediate_size",
                "num_hidden_layers",
                "max_position_embeddings",
                "n_layer",
                "n_embd",
                "n_inner",
            )
            if k in claims
        },
        "tokenizer": {
            "class": tokenizer.get("tokenizer_class"),
            "template_names": names,
        },
        "source_sha256": source_identity(),
        "projection_support_verified": False,
        "layers": [],
        "hosted_text_submitted": False,
    }
    if job.get("load"):
        runtime = LocalRuntime(path, job.get("device", "auto"))
        reply.update(model_loaded=True, runtime=runtime.info)
        try:
            blocks = transformer_blocks(runtime.model)
        except NeuronSteerError as exc:
            reply["layout_diagnostic"] = str(exc)
            return reply
        if len(blocks) > 256:
            raise NeuronSteerError(
                "model exceeds the supported 256-layer inspection bound"
            )
        for layer, block in enumerate(blocks):
            try:
                info = projection_info(transformer_down_proj(block))
                reply["layers"].append({"layer": layer, "supported": True, **info})
            except NeuronSteerError as exc:
                reply["layers"].append(
                    {"layer": layer, "supported": False, "reason": str(exc)}
                )
        reply["projection_support_verified"] = bool(reply["layers"]) and all(
            row["supported"] for row in reply["layers"]
        )
    return reply


def probe_runtime(runtime, job, rows):
    torch = runtime.torch
    blocks = transformer_blocks(runtime.model)
    layers = job["layers"]
    if any(layer >= len(blocks) for layer in layers):
        raise NeuronSteerError("selected layer is outside the loaded model")
    profiles, norms, layouts, records = {}, {}, {}, []
    for layer in layers:
        module = transformer_down_proj(blocks[layer])
        layouts[layer] = info = projection_info(module)
        if info["mlp_width"] > 1000001:
            raise NeuronSteerError("projection exceeds supported neuron coordinates")
        weight = module.weight.detach().float()
        norm = torch.linalg.vector_norm(
            weight, dim=1 if info["orientation"] == "input_output" else 0
        )
        if not bool(torch.isfinite(norm).all().item()):
            raise NeuronSteerError("non-finite projection weights")
        norms[layer] = norm.cpu()
        del weight, norm
        profiles[layer] = {}
    for row in rows:
        rendered, formatting = prepare_chat_prompt(
            runtime.tokenizer,
            row["prompt"],
            job.get("no_thinking", True),
            job.get("system_role", "auto"),
            job.get("chat_template"),
        )
        encoded = runtime.tokenizer(
            rendered,
            return_tensors="pt",
            add_special_tokens=formatting["add_special_tokens"],
        )
        tokens = int(encoded["input_ids"].shape[-1])
        bound = getattr(runtime.model.config, "max_position_embeddings", 0)
        if tokens < 1:
            raise NeuronSteerError("probe requires at least one formatted input token")
        if tokens > job["max_input_tokens"] or (
            type(bound) is int and bound > 0 and tokens > bound
        ):
            raise NeuronSteerError(
                "probe input exceeds token/context bound; input is never silently truncated"
            )
        token_ids = encoded["input_ids"][0].tolist()
        seen, handles = {}, []
        try:
            for layer in layers:

                def observe(module, inputs, output, layer=layer):
                    if layer in seen or not inputs:
                        raise NeuronSteerError(
                            "projection must execute exactly once per probe forward"
                        )
                    values = inputs[0]
                    if (
                        values.ndim != 3
                        or values.shape[0] != 1
                        or values.shape[1] != tokens
                        or values.shape[2] != layouts[layer]["mlp_width"]
                    ):
                        raise NeuronSteerError("unexpected projection input shape")
                    values = values[0].detach().float()
                    policy = job.get("positions", "last")
                    values = (
                        values[-1:]
                        if policy == "last"
                        else values[1:]
                        if policy == "skip-first" and tokens > 1
                        else values
                    )
                    if not bool(torch.isfinite(values).all().item()):
                        raise NeuronSteerError("non-finite projection input")
                    seen[layer] = (
                        values.mean(dim=0).cpu(),
                        values.abs().mean(dim=0).cpu(),
                        int(values.shape[0]),
                    )

                handles.append(
                    transformer_down_proj(blocks[layer]).register_forward_hook(observe)
                )
            torch.manual_seed(0)
            with torch.inference_mode():
                runtime.model(
                    **{k: v.to(runtime.device) for k, v in encoded.items()},
                    use_cache=False,
                )
            if set(seen) != set(layers):
                raise NeuronSteerError("selected projection did not execute")
        finally:
            for handle in handles:
                handle.remove()
        for layer, (mean, absolute, count) in seen.items():
            group = profiles[layer].setdefault(
                row["category"],
                {
                    "count": 0,
                    "mean": torch.zeros_like(mean),
                    "absolute": torch.zeros_like(absolute),
                },
            )
            group["count"] += 1
            group["mean"] += mean
            group["absolute"] += absolute
        records.append(
            {
                **row,
                "input_tokens": tokens,
                "positions_used": {str(layer): seen[layer][2] for layer in layers},
                "rendered_prompt": rendered,
                "formatting": formatting,
                "input_token_ids": token_ids,
                "input_token_sha256": json_digest(token_ids),
            }
        )
    results = []
    for layer in layers:
        groups = profiles[layer]
        total = sum(g["count"] for g in groups.values())
        mean = sum(g["mean"] for g in groups.values()) / total
        absolute = sum(g["absolute"] for g in groups.values()) / total
        positive, negative = job.get("positive"), job.get("negative")
        contrast = (
            (
                groups[positive]["mean"] / groups[positive]["count"]
                - groups[negative]["mean"] / groups[negative]["count"]
            )
            if positive is not None
            else None
        )
        scores = (contrast.abs() if contrast is not None else absolute) * norms[layer]
        if not bool(torch.isfinite(scores).all().item()):
            raise NeuronSteerError("non-finite probe summary")
        # Stable sorting gives tied scores ascending neuron indices.
        ranked = torch.argsort(scores, descending=True, stable=True)[
            : job["top_k"]
        ].tolist()
        candidates = []
        for neuron in ranked:
            candidates.append(
                {
                    "layer": layer,
                    "neuron": neuron,
                    "coordinate": f"{layer}:{neuron}",
                    "score": float(scores[neuron]),
                    "mean_activation": float(mean[neuron]),
                    "mean_absolute_activation": float(absolute[neuron]),
                    "projection_norm": float(norms[layer][neuron]),
                    "contrast_activation_delta": float(contrast[neuron])
                    if contrast is not None
                    else None,
                    "category_mean_activation": {
                        name: float(g["mean"][neuron] / g["count"])
                        for name, g in groups.items()
                    },
                }
            )
        results.append({"layer": layer, **layouts[layer], "candidates": candidates})
    return {
        "operation": "probe",
        "model_loaded": True,
        "generated_tokens": 0,
        "seed": 0,
        "steering_applied": False,
        "runtime": runtime.info,
        "records": records,
        "layers": results,
        "category_counts": {
            name: g["count"] for name, g in profiles[layers[0]].items()
        },
        "ranking": "absolute signed-category-mean difference × projection norm"
        if job.get("positive") is not None
        else "mean absolute activation × projection norm",
        "prompt_weighting": "equal per prompt; selected positions averaged within each prompt",
        "positions": job.get("positions", "last"),
        "observational_only": True,
        "causality_verified": False,
        "hosted_text_submitted": False,
        "neuronpedia_references": job.get("references", []),
        "references_are_annotations": True,
    }


def run_research(job):
    rows = validate_research_job(job)
    if job["operation"] == "inspect":
        return inspect_model(job)
    identity_job = {
        **job,
        "neurons": [],
        "max_tokens": 0,
        "temperature": 0.0,
        "context_size": job["max_input_tokens"],
        "seed": 0,
        "no_thinking": job.get("no_thinking", True),
        "device": job.get("device", "auto"),
    }
    experiment = manifest(identity_job, rows)
    experiment["probe_settings"] = {
        key: job.get(key)
        for key in (
            "layers",
            "top_k",
            "limit",
            "max_input_tokens",
            "positions",
            "positive",
            "negative",
        )
    }
    runtime = LocalRuntime(job["model"], job.get("device", "auto"))
    return {
        **probe_runtime(runtime, job, rows),
        "experiment_manifest": experiment,
        "experiment_fingerprint": digest(experiment),
    }
