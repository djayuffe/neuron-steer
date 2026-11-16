"""Local Transformers runtime with explicit prompt policy and execution evidence."""

import copy
import hashlib
import json
import os
import pathlib
import time

from .steering import (
    ModelContextOverflow,
    NeuronSteerError,
    install_steering,
    validate_generation_options,
    validate_prompt,
)

SYSTEM_MESSAGE = (
    "You are a local text-generation model. No tools or external actions are available."
)


def prepare_chat_prompt(
    tokenizer, text, no_thinking=True, system_role="auto", chat_template=None
):
    if system_role not in ("auto", "merge", "strict"):
        raise NeuronSteerError("system-role policy must be auto, merge or strict")
    template = getattr(tokenizer, "chat_template", None)
    selected = None
    if isinstance(template, dict):
        selected = chat_template or (
            "default"
            if "default" in template
            else next(iter(template))
            if len(template) == 1
            else None
        )
        if selected not in template:
            raise NeuronSteerError(
                "select an available named chat template with --chat-template"
            )
        template = template[selected]
    elif chat_template not in (None, "default"):
        raise NeuronSteerError("this tokenizer has no named chat template")
    if template is not None and not isinstance(template, str):
        raise NeuronSteerError("chat template must resolve to text")
    metadata = {
        "system_message": SYSTEM_MESSAGE,
        "system_role_requested": system_role,
        "selected_template": selected,
        "template": template,
        "thinking_requested": not no_thinking,
        "thinking_control_passed": bool(template and "enable_thinking" in template),
        "thinking_effect_verified": False,
    }
    if not template:
        metadata.update(
            format="plain", system_role_effective="plain", add_special_tokens=True
        )
        return (
            "System:\n" + SYSTEM_MESSAGE + "\n\nUser:\n" + text + "\n\nAssistant:\n",
            metadata,
        )
    kwargs = {"chat_template": template}
    if metadata["thinking_control_passed"]:
        kwargs["enable_thinking"] = not no_thinking

    def render(merge):
        messages = (
            [{"role": "user", "content": SYSTEM_MESSAGE + "\n\n" + text}]
            if merge
            else [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": text},
            ]
        )
        return tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, **kwargs
        )

    merged = system_role == "merge"
    try:
        prompt = render(merged)
    except Exception as exc:
        if type(exc).__name__ == "TemplateSyntaxError":
            raise
        description = str(exc).casefold()
        template_error = type(exc).__module__.startswith("jinja2.") or isinstance(
            exc, (ValueError, TypeError)
        )
        role_rejection = (
            (
                "system" in description
                and any(
                    term in description
                    for term in (
                        "not support",
                        "unsupported",
                        "not allowed",
                        "only user",
                        "disallowed",
                    )
                )
            )
            or ("roles" in description and "alternate" in description)
            or (
                "only" in description
                and "user" in description
                and "assistant" in description
                and "role" in description
            )
        )
        if system_role != "auto" or not template_error or not role_rejection:
            raise
        prompt = render(True)
        merged = True
        metadata["fallback_reason"] = "system_role_rejected"
    metadata.update(
        format="chat_template",
        system_role_effective="merged_user" if merged else "system",
        add_special_tokens=False,
    )
    return prompt, metadata


def build_chat_prompt(tokenizer, text, no_thinking=True):
    return prepare_chat_prompt(tokenizer, text, no_thinking)[0]


def json_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def snapshot_inputs(encoded):
    """Capture every tokenizer tensor, preserving values, shape and dtype."""
    return {
        key: {"dtype": str(tensor.dtype), "values": tensor.tolist()}
        for key, tensor in encoded.items()
    }


class LocalRuntime:
    def __init__(self, model_path, device="auto"):
        path = pathlib.Path(model_path).expanduser().resolve()
        if not path.is_dir():
            raise NeuronSteerError(
                "model must be an existing local Transformers directory"
            )
        if device not in ("auto", "cpu", "mps", "cuda"):
            raise NeuronSteerError("unsupported device")
        os.environ.update(
            HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1"
        )
        try:
            import torch
            import transformers
        except ImportError as exc:
            raise NeuronSteerError(
                "install the optional runtime dependencies in the selected Python environment"
            ) from exc
        self.torch = torch
        if device == "auto":
            device = (
                "cuda"
                if torch.cuda.is_available()
                else "mps"
                if hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
                else "cpu"
            )
        self.device = device
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False
        )
        self.model = transformers.AutoModelForCausalLM.from_pretrained(
            str(path),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        )
        self.model.to(device)
        self.model.eval()
        self.info = {
            "python_model_path": str(path),
            "device": device,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "dtype": str(self.model.dtype),
            "model_class": type(self.model).__module__
            + "."
            + type(self.model).__qualname__,
            "tokenizer_class": type(self.tokenizer).__module__
            + "."
            + type(self.tokenizer).__qualname__,
            "offline_loading": True,
        }

    def generate(
        self,
        text,
        specs,
        *,
        max_tokens=64,
        temperature=0.0,
        context_size=0,
        seed=0,
        no_thinking=True,
        system_role="auto",
        chat_template=None,
        calibration="skip-first",
        control="target",
        control_seed=0,
    ):
        validate_prompt(text)
        validate_generation_options(
            max_tokens=max_tokens,
            temperature=temperature,
            context_size=context_size,
            seed=seed,
        )
        prompt, formatting = prepare_chat_prompt(
            self.tokenizer, text, no_thinking, system_role, chat_template
        )
        encoded = self.tokenizer(
            prompt,
            return_tensors="pt",
            add_special_tokens=formatting["add_special_tokens"],
        )
        input_tokens = encoded["input_ids"].shape[-1]
        supported = getattr(self.model.config, "max_position_embeddings", 0)
        supported = supported if type(supported) is int and supported > 0 else 0
        bound = (
            min(context_size, supported)
            if context_size and supported
            else context_size or supported
        )
        if bound and input_tokens + max_tokens > bound:
            raise ModelContextOverflow(
                "input plus completion exceeds the context bound"
            )
        input_ids = (
            encoded["input_ids"][0].tolist()
            if hasattr(encoded["input_ids"][0], "tolist")
            else None
        )
        model_inputs = snapshot_inputs(encoded)
        encoded = {k: v.to(self.device) for k, v in encoded.items()}
        options = {
            "max_new_tokens": max_tokens,
            "do_sample": temperature > 0,
            "return_dict_in_generate": False,
            "num_beams": 1,
            "num_return_sequences": 1,
            "output_scores": False,
            "output_hidden_states": False,
            "output_attentions": False,
        }
        if temperature > 0:
            options["temperature"] = temperature
        config = copy.deepcopy(self.model.generation_config)
        for key, value in options.items():
            setattr(config, key, value)
        effective = (
            config.to_dict()
            if callable(getattr(config, "to_dict", None))
            else {**vars(config), **options}
        )
        if (
            type(effective.get("min_new_tokens")) is int
            and effective["min_new_tokens"] > max_tokens
        ):
            raise NeuronSteerError(
                "model min_new_tokens exceeds the requested completion budget"
            )
        kwargs = (
            {"generation_config": config}
            if callable(getattr(config, "to_dict", None))
            else options
        )
        if effective.get("stop_strings"):
            kwargs["tokenizer"] = self.tokenizer
        started = time.monotonic()
        calibration_seconds = 0.0
        result, handles = None, []
        try:
            if specs:
                result, handles = install_steering(
                    self.model,
                    encoded,
                    specs,
                    self.torch,
                    calibration=calibration,
                    control=control,
                    control_seed=control_seed,
                )
                calibration_seconds = time.monotonic() - started
            if seed is not None:
                self.torch.manual_seed(seed)
            with self.torch.inference_mode():
                output = self.model.generate(**encoded, **kwargs)
            if len(output) != 1:
                raise NeuronSteerError("generation must return exactly one sequence")
            ids = output[0][input_tokens:]
            if specs and not result["applied"]:
                raise NeuronSteerError(
                    "steering target was installed but never executed during generation"
                )
            raw_content = self.tokenizer.decode(ids, skip_special_tokens=True)
            output_tokens = len(ids)
            eos = effective.get("eos_token_id")
            eos = eos if isinstance(eos, (list, tuple)) else [eos]
            last = int(ids[-1]) if output_tokens else None
            budget_hit = output_tokens >= max_tokens
            output_ids = ids.tolist() if hasattr(ids, "tolist") else list(ids)
            manifest = {
                "prompt": text,
                "rendered_prompt": prompt,
                "formatting": formatting,
                "input_token_ids": input_ids,
                "model_inputs": model_inputs,
                "model_inputs_sha256": json_digest(model_inputs),
                "input_token_sha256": json_digest(input_ids)
                if input_ids is not None
                else None,
                "generation_config": effective,
                "requested": {
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "context_size": context_size,
                    "seed": seed,
                    "no_thinking": no_thinking,
                    "system_role": system_role,
                    "chat_template": chat_template,
                    "calibration": calibration,
                    "control": control,
                    "control_seed": control_seed,
                },
            }
            return {
                "content": raw_content.strip(),
                "raw_content": raw_content,
                "tokens": output_tokens,
                "input_tokens": input_tokens,
                "output_token_ids": output_ids,
                "ended_with_eos": last in eos and output_tokens > 0,
                "reached_token_budget": budget_hit,
                "possibly_truncated": budget_hit,
                "stop_reason": "at_token_limit"
                if budget_hit
                else "eos"
                if last in eos
                else "other",
                "stop_reason_is_inferred": True,
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "calibration_seconds": round(calibration_seconds, 4),
                "steering": result,
                "manifest": manifest,
            }
        finally:
            for handle in handles:
                handle.remove()
