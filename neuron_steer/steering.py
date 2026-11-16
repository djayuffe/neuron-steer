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


def formula(calibration="skip-first") -> str:
    """Return the human-readable steering formula."""
    positions = (
        "all prompt tokens"
        if calibration == "all"
        else "prompt tokens 1..end (or token 0 for a one-token prompt)"
    )
    return (
        f"For each target: add dose * median(||residual leaving layer L||, {positions}) "
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


def projection_info(module):
    weight = getattr(module, "weight", None)
    if getattr(weight, "ndim", 0) != 2:
        raise NeuronSteerError("projection requires a dense 2-D weight")
    floating = getattr(weight, "is_floating_point", None)
    if getattr(weight, "is_quantized", False) or (
        callable(floating) and not floating()
    ):
        raise NeuronSteerError("projection requires floating-point weights")
    transposed = (hasattr(module, "nx") and hasattr(module, "nf")) or (
        type(module).__name__ == "Conv1D" and hasattr(module, "nf")
    )
    rows, cols = map(int, weight.shape)
    width, residual = (rows, cols) if transposed else (cols, rows)
    if (
        width <= 0
        or residual <= 0
        or int(getattr(module, "nx" if transposed else "in_features", width)) != width
    ):
        raise NeuronSteerError("unsupported projection dimensions")
    return {
        "mlp_width": width,
        "residual_width": residual,
        "orientation": "input_output" if transposed else "output_input",
        "weight_shape": [rows, cols],
    }


def install_steering(
    model: Any,
    encoded: Any,
    specs: list[dict[str, Any]],
    torch: Any,
    *,
    calibration="skip-first",
    control="target",
    control_seed=0,
):
    """Calibrate and install temporary ``down_proj`` hooks.

    Returns ``(steering_result, handles)``. The caller must remove every handle
    when generation finishes. The returned result confirms that the hooks were
    applied and reports the per-target calibration values.
    """
    if calibration not in ("skip-first", "all"):
        raise NeuronSteerError("calibration must be skip-first or all")
    if control not in ("target", "random"):
        raise NeuronSteerError("unknown intervention control")
    blocks = transformer_blocks(model)
    calibration_policy = calibration
    calibration: dict[int, float] = {}
    calibration_counts = {}
    handles: list[Any] = []

    def make_calibration_hook(layer: int):
        def hook(module, inputs, output):
            hidden = output[0] if isinstance(output, tuple) else output
            if getattr(hidden, "ndim", 0) != 3:
                raise NeuronSteerError(
                    "unexpected decoder output shape during steering calibration"
                )
            tokens = (
                hidden[0, 1:, :]
                if calibration_policy == "skip-first" and hidden.shape[1] > 1
                else hidden[0]
            )
            norms = torch.linalg.vector_norm(tokens.float(), dim=-1)
            calibration[layer] = float(torch.median(norms).item())
            calibration_counts[layer] = {
                "batch_element": 0,
                "positions_used": hidden.shape[1]
                - (
                    1
                    if calibration_policy == "skip-first" and hidden.shape[1] > 1
                    else 0
                ),
                "policy": calibration_policy,
            }

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
    report = {
        "installed": False,
        "applied": False,
        "method": "down_proj_add",
        "calibration": calibration_policy,
        "control": control,
        "calibration_counts": calibration_counts,
        "neurons": plans,
        "layers": [],
    }
    vectors_by_layer = {}

    steering_handles: list[Any] = []
    try:
        for spec in specs:
            layer, neuron, dose = spec["layer"], spec["neuron"], spec["dose"]
            down = transformer_down_proj(blocks[layer])
            layout = projection_info(down)
            weight = down.weight
            if not 0 <= neuron < layout["mlp_width"]:
                raise NeuronSteerError(
                    f"neuron {neuron} is outside layer {layer} down_proj"
                )
            column = (
                weight[neuron, :]
                if layout["orientation"] == "input_output"
                else weight[:, neuron]
            ).detach()
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

            if control == "random":
                if not hasattr(torch, "Generator"):
                    raise NeuronSteerError(
                        "random controls require a real tensor runtime"
                    )
                generator = torch.Generator(device="cpu").manual_seed(
                    int(control_seed) + layer * 1000003 + neuron
                )
                direction = torch.randn(
                    tuple(column.shape), generator=generator, dtype=torch.float32
                ).to(column.device)
                direction_norm = torch.linalg.vector_norm(direction).item()
                if not math.isfinite(direction_norm) or direction_norm <= 0:
                    raise NeuronSteerError("invalid random control direction")
                vector = (direction * (dose * median_norm / direction_norm)).to(
                    dtype=column.dtype
                )
                if not bool(torch.isfinite(vector).all().item()):
                    raise NeuronSteerError("random control overflows projection dtype")
            achieved = float(torch.linalg.vector_norm(vector.float()).item())
            plan = {
                "layer": layer,
                "neuron": neuron,
                "dose": dose,
                "median_residual_norm": median_norm,
                "down_proj_column_norm": column_norm,
                "scale": scale if control == "target" else None,
                "requested_injection_norm": abs(dose) * median_norm,
                "achieved_injection_norm": achieved,
                "calls": 0,
                "positions_touched": 0,
                "first_call_positions": 0,
                "later_call_positions": 0,
            }
            plans.append(plan)
            vectors_by_layer.setdefault(layer, []).append(vector)

            def hook(module, inputs, output, vector=vector, plan=plan):
                hidden = output[0] if isinstance(output, tuple) else output
                if getattr(hidden, "ndim", 0) != 3:
                    raise NeuronSteerError("unexpected projection output shape")
                added = hidden + vector.to(dtype=hidden.dtype, device=hidden.device)
                if hasattr(torch, "isfinite") and not bool(
                    torch.isfinite(added).all().item()
                ):
                    raise NeuronSteerError("non-finite steered projection output")
                positions = int(hidden.shape[0] * hidden.shape[1])
                plan[
                    "first_call_positions"
                    if plan["calls"] == 0
                    else "later_call_positions"
                ] += positions
                plan["positions_touched"] += positions
                plan["calls"] += 1
                report["applied"] = all(p["calls"] > 0 for p in plans)
                return (added, *output[1:]) if isinstance(output, tuple) else added

            steering_handles.append(down.register_forward_hook(hook))
        if hasattr(torch, "stack"):
            for layer, vectors in vectors_by_layer.items():
                combined = torch.stack([v.float() for v in vectors]).sum(dim=0)
                cast = combined.to(dtype=vectors[0].dtype)
                if not bool(torch.isfinite(cast).all().item()):
                    raise NeuronSteerError(
                        "combined intervention overflows projection dtype"
                    )
                norm = float(torch.linalg.vector_norm(cast.float()).item())
                report["layers"].append(
                    {
                        "layer": layer,
                        "combined_injection_norm": norm,
                        "combined_to_residual_ratio": norm / calibration[layer]
                        if calibration[layer]
                        else 0.0,
                    }
                )
    except Exception:
        for handle in steering_handles:
            handle.remove()
        raise

    report["installed"] = True
    return report, steering_handles
