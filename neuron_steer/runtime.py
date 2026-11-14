"""Optional local Transformers runtime. Imports and weights are loaded on demand."""

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


def build_chat_prompt(tokenizer, text, no_thinking=True):
    messages = [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": text},
    ]
    template = getattr(tokenizer, "chat_template", None)
    kwargs = (
        {"enable_thinking": False}
        if no_thinking and template and "enable_thinking" in template
        else {}
    )
    if template:
        try:
            return tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, **kwargs
            )
        except (ValueError, TypeError):
            return tokenizer.apply_chat_template(
                [{"role": "user", "content": SYSTEM_MESSAGE + "\n\n" + text}],
                tokenize=False,
                add_generation_prompt=True,
                **kwargs,
            )
    return "System:\n" + SYSTEM_MESSAGE + "\n\nUser:\n" + text + "\n\nAssistant:\n"


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
    ):
        validate_prompt(text)
        validate_generation_options(
            max_tokens=max_tokens,
            temperature=temperature,
            context_size=context_size,
            seed=seed,
        )
        prompt = build_chat_prompt(self.tokenizer, text, no_thinking)
        encoded = self.tokenizer(prompt, return_tensors="pt")
        input_tokens = encoded["input_ids"].shape[-1]
        supported = getattr(self.model.config, "max_position_embeddings", 0)
        supported = supported if isinstance(supported, int) and supported > 0 else 0
        bound = (
            min(context_size, supported)
            if context_size and supported
            else context_size or supported
        )
        if bound and input_tokens + max_tokens > bound:
            raise ModelContextOverflow(
                "input plus completion exceeds the context bound"
            )
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        options = {
            "max_new_tokens": max_tokens,
            "do_sample": temperature > 0,
            "return_dict_in_generate": False,
        }
        if temperature > 0:
            options["temperature"] = temperature
        started = time.monotonic()
        result, handles = None, []
        try:
            if specs:
                result, handles = install_steering(
                    self.model, encoded, specs, self.torch
                )
            if seed is not None:
                self.torch.manual_seed(seed)
            with self.torch.inference_mode():
                output = self.model.generate(**encoded, **options)
            ids = output[0][input_tokens:]
            content = self.tokenizer.decode(ids, skip_special_tokens=True)
            output_tokens = len(ids)
            eos = getattr(self.model.generation_config, "eos_token_id", None)
            eos = eos if isinstance(eos, (tuple, list)) else [eos]
            last = int(ids[-1]) if output_tokens else None
            return {
                "content": content.strip(),
                "tokens": output_tokens,
                "input_tokens": input_tokens,
                "reached_token_budget": output_tokens >= max_tokens and last not in eos,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "steering": result,
            }
        finally:
            for handle in handles:
                handle.remove()
