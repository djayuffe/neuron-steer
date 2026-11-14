"""Bounded ``down_proj`` neuron steering for Transformers models.

The recipe implemented here follows the public ``down_proj`` steering
description:

```text
For each target layer L and neuron N:
  scale = dose * median(||residual leaving layer L||, prompt tokens 1..end)
                / ||W_down[L][:, N]||
  add scale * W_down[L][:, N] to the down_proj output at every token
```

Only explicit numeric targets are accepted. No named "evil" or "safe" presets
are bundled, and the module never touches model weights on disk.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

MAX_STEERS = 8
MAX_LAYER = 255
MAX_INDEX = 1_000_000
MAX_DOSE = 10.0
MAX_PROMPT_CHARS = 10_000
MAX_PROMPT_BYTES = 40_000

MAX_TOKENS = 32_768
MAX_CONTEXT_SIZE = 1_048_576
MAX_SEED = 100_000_000


class NeuronSteerError(Exception):
    """A user-facing validation or steering error."""


class ModelContextOverflow(NeuronSteerError):
    """The prompt plus completion budget exceeds the model context bound."""


def formula() -> str:
    """Return the human-readable steering formula."""
    return (
        "For each target: add dose * median(||residual leaving layer L||, prompt tokens 1..end) "
        "/ ||W_down[L][:, N]|| * W_down[L][:, N] to the down_proj output at every token."
    )


def parse_specs(values: Iterable[str] | None) -> list[dict[str, Any]]:
    """Parse repeatable ``LAYER:NEURON:DOSE`` targets with hard bounds."""
    values = list(values or [])
    if not values:
        raise NeuronSteerError(
            "at least one --neuron LAYER:NEURON:DOSE target is required"
        )
    if len(values) > MAX_STEERS:
        raise NeuronSteerError(f"at most {MAX_STEERS} neuron targets are supported")

    specs: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for value in values:
        parts = value.split(":") if isinstance(value, str) else []
        if len(parts) != 3 or any(not part.strip() for part in parts):
            raise NeuronSteerError(
                "--neuron must be LAYER:NEURON:DOSE, for example 14:8949:1.0"
            )
        try:
            layer, neuron, dose = int(parts[0]), int(parts[1]), float(parts[2])
        except ValueError as exc:
            raise NeuronSteerError(
                "--neuron layer and neuron must be integers; dose must be a number"
            ) from exc
        if not 0 <= layer <= MAX_LAYER:
            raise NeuronSteerError(f"--neuron layer must be 0-{MAX_LAYER}")
        if not 0 <= neuron <= MAX_INDEX:
            raise NeuronSteerError(f"--neuron index must be 0-{MAX_INDEX}")
        if not math.isfinite(dose) or abs(dose) > MAX_DOSE:
            raise NeuronSteerError(
                f"--neuron dose must be finite and between -{MAX_DOSE:g} and {MAX_DOSE:g}"
            )
        key = (layer, neuron)
        if key in seen:
            raise NeuronSteerError(f"--neuron target {layer}:{neuron} is duplicated")
        seen.add(key)
        specs.append({"layer": layer, "neuron": neuron, "dose": dose})
    return specs


def validate_prompt(prompt: Any) -> str:
    """Validate an explicit prompt string and its encoded size."""
    if not isinstance(prompt, str):
        raise NeuronSteerError("prompt must be text")
    try:
        encoded = prompt.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NeuronSteerError("prompt must be valid UTF-8") from exc
    if (
        not prompt.strip()
        or len(prompt) > MAX_PROMPT_CHARS
        or len(encoded) > MAX_PROMPT_BYTES
    ):
        raise NeuronSteerError(
            f"prompt must be 1-{MAX_PROMPT_CHARS} characters / {MAX_PROMPT_BYTES} UTF-8 bytes"
        )
    return prompt


def validate_generation_options(
    *, max_tokens: int, temperature: float, context_size: int, seed: int | None
) -> None:
    """Validate generation settings shared by the CLI and evaluation harness."""
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_TOKENS:
        raise NeuronSteerError(f"--max-tokens must be 1-{MAX_TOKENS}")
    if (
        type(temperature) not in (int, float)
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise NeuronSteerError("--temperature must be finite and 0-2")
    if type(context_size) is not int or not 0 <= context_size <= MAX_CONTEXT_SIZE:
        raise NeuronSteerError(f"--context-size must be 0-{MAX_CONTEXT_SIZE}")
    if seed is not None and (
        type(seed) is not int or not -MAX_SEED <= seed <= MAX_SEED
    ):
        raise NeuronSteerError(f"--seed must be between -{MAX_SEED} and {MAX_SEED}")


def transformer_blocks(model: Any) -> Any:
    """Locate the decoder block list for common causal-LM layouts."""
    for path in (("model", "layers"), ("transformer", "h"), ("gpt_neox", "layers")):
        node = model
        for attribute in path:
            node = getattr(node, attribute, None)
            if node is None:
                break
        if node is not None:
            return node
    raise NeuronSteerError("unsupported model layout for neuron steering")


def transformer_down_proj(block: Any) -> Any:
    """Locate the MLP output projection in a decoder block."""
    mlp = getattr(block, "mlp", None)
    for name in ("down_proj", "c_proj", "dense_4h_to_h"):
        module = getattr(mlp, name, None) if mlp is not None else None
        if module is not None and hasattr(module, "weight"):
            return module
    raise NeuronSteerError("model block has no supported down_proj weight")


def install_steering(model: Any, encoded: Any, specs: list[dict[str, Any]], torch: Any):
    """Calibrate and install temporary ``down_proj`` hooks.

    Returns ``(steering_result, handles)``. The caller must remove every handle
    when generation finishes. The returned result confirms that the hooks were
    applied and reports the per-target calibration values.
    """
    blocks = transformer_blocks(model)
    calibration: dict[int, float] = {}
    handles: list[Any] = []

    def make_calibration_hook(layer: int):
        def hook(module, inputs, output):
            hidden = output[0] if isinstance(output, tuple) else output
            if getattr(hidden, "ndim", 0) != 3:
                raise NeuronSteerError(
                    "unexpected decoder output shape during steering calibration"
                )
            tokens = hidden[0, 1:, :] if hidden.shape[1] > 1 else hidden[0]
            norms = torch.linalg.vector_norm(tokens.float(), dim=-1)
            calibration[layer] = float(torch.median(norms).item())

        return hook

    try:
        for layer in sorted({spec["layer"] for spec in specs}):
            if not 0 <= layer < len(blocks):
                raise NeuronSteerError(f"steering layer {layer} is outside the model")
            handles.append(
                blocks[layer].register_forward_hook(make_calibration_hook(layer))
            )
        with torch.inference_mode():
            model(**encoded)
    finally:
        for handle in handles:
            handle.remove()

    plans: list[dict[str, Any]] = []
    steering_handles: list[Any] = []
    try:
        for spec in specs:
            layer, neuron, dose = spec["layer"], spec["neuron"], spec["dose"]
            down = transformer_down_proj(blocks[layer])
            weight = getattr(down, "weight", None)
            if getattr(weight, "ndim", 0) != 2:
                raise NeuronSteerError(
                    "neuron steering requires a 2-D down_proj weight (not quantized)"
                )
            if hasattr(down, "nx") and hasattr(down, "nf"):
                # GPT-2 Conv1D stores weights as (input, output); select a row.
                input_dim = int(down.nx)
                if weight.shape[0] != input_dim:
                    raise NeuronSteerError("unsupported down_proj weight orientation")
                if not 0 <= neuron < input_dim:
                    raise NeuronSteerError(
                        f"neuron {neuron} is outside layer {layer} down_proj"
                    )
                column = weight[neuron, :].detach()
            else:
                input_dim = int(getattr(down, "in_features", weight.shape[1]))
                if weight.shape[1] != input_dim:
                    raise NeuronSteerError("unsupported down_proj weight orientation")
                if not 0 <= neuron < input_dim:
                    raise NeuronSteerError(
                        f"neuron {neuron} is outside layer {layer} down_proj"
                    )
                column = weight[:, neuron].detach()
            floating = getattr(weight, "is_floating_point", None)
            if getattr(weight, "is_quantized", False) or (
                callable(floating) and not floating()
            ):
                raise NeuronSteerError(
                    "neuron steering requires dense floating-point projection weights"
                )
            column_norm = float(torch.linalg.vector_norm(column.float()).item())
            if not math.isfinite(column_norm) or column_norm <= 0:
                raise NeuronSteerError(
                    "neuron steering target has an invalid down_proj column norm"
                )
            median_norm = calibration[layer]
            if not math.isfinite(median_norm) or median_norm < 0:
                raise NeuronSteerError("invalid residual calibration norm")
            scale = dose * median_norm / column_norm
            if not math.isfinite(scale):
                raise NeuronSteerError("non-finite steering scale")
            vector = (column.float() * scale).to(
                dtype=column.dtype, device=column.device
            )
            if hasattr(torch, "isfinite") and not bool(
                torch.isfinite(vector).all().item()
            ):
                raise NeuronSteerError("steering vector overflows the projection dtype")

            def hook(module, inputs, output, vector=vector):
                if isinstance(output, tuple):
                    hidden = output[0]
                    return (
                        hidden + vector.to(dtype=hidden.dtype, device=hidden.device),
                        *output[1:],
                    )
                return output + vector.to(dtype=output.dtype, device=output.device)

            steering_handles.append(down.register_forward_hook(hook))
            plans.append(
                {
                    "layer": layer,
                    "neuron": neuron,
                    "dose": dose,
                    "median_residual_norm": median_norm,
                    "down_proj_column_norm": column_norm,
                    "scale": scale,
                }
            )
    except Exception:
        for handle in steering_handles:
            handle.remove()
        raise

    return (
        {
            "applied": True,
            "method": "down_proj_add",
            "calibration": "prompt_tokens_1_onward",
            "neurons": plans,
        },
        steering_handles,
    )
