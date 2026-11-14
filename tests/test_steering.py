import contextlib
import types
import unittest
from neuron_steer.steering import (
    parse_specs,
    install_steering,
    NeuronSteerError,
    validate_prompt,
    validate_generation_options,
)


class FakeScalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class FakeColumn:
    def __init__(self, length, norm, value=1.0):
        self.shape = (length,)
        self.ndim = 1
        self.norm = norm
        self.value = value
        self.dtype = "float32"
        self.device = "cpu"

    def detach(self):
        return self

    def float(self):
        return self

    def __mul__(self, scalar):
        return FakeColumn(self.shape[0], self.norm, self.value * scalar)

    def to(self, dtype=None, device=None):
        return self


class FakeTensor:
    def __init__(self, shape, value=0.0, norm=3.0):
        self.shape = tuple(shape)
        self.ndim = len(self.shape)
        self.value = value
        self.norm = norm
        self.dtype = "float32"
        self.device = "cpu"
        self.added = None

    def __getitem__(self, key):
        if isinstance(key, tuple) and len(key) == 2 and key[0] == slice(None):
            return FakeColumn(self.shape[0], norm=2.0)
        if (
            isinstance(key, tuple)
            and len(key) == 2
            and isinstance(key[0], int)
            and key[1] == slice(None)
        ):
            return FakeColumn(self.shape[1], norm=2.0)
        if isinstance(key, tuple) and len(key) == 3 and key[0] == 0:
            return FakeTensor(
                (1, self.shape[1] - 1, self.shape[2]), self.value, self.norm
            )
        if key == 0:
            return FakeTensor((self.shape[1], self.shape[2]), self.value, self.norm)
        raise IndexError(key)

    def __add__(self, other):
        result = FakeTensor(
            self.shape, self.value + getattr(other, "value", 0.0), self.norm
        )
        result.added = other
        return result

    def __mul__(self, scalar):
        return FakeTensor(self.shape, self.value * scalar, self.norm)

    def detach(self):
        return self

    def float(self):
        return self

    def to(self, dtype=None, device=None):
        return self


class FakeHook:
    def __init__(self, function):
        self.function = function
        self.removed = False

    def remove(self):
        self.removed = True


class FakeDown:
    def __init__(self):
        self.weight = FakeTensor((4, 8))
        self.hooks = []

    def register_forward_hook(self, function):
        hook = FakeHook(function)
        self.hooks.append(hook)
        return hook


class FakeConvDown(FakeDown):
    """GPT-2 Conv1D-style weight: (input, output) instead of (output, input)."""

    def __init__(self):
        self.weight = FakeTensor((8, 4))
        self.nx = 8
        self.nf = 4
        self.hooks = []


class FakeBlock:
    def __init__(self, down):
        self.mlp = types.SimpleNamespace(down_proj=down)
        self.hooks = []
        self.output = FakeTensor((1, 3, 4))

    def register_forward_hook(self, function):
        hook = FakeHook(function)
        self.hooks.append(hook)
        return hook

    def __call__(self):
        for hook in self.hooks:
            hook.function(self, (), self.output)
        return self.output


class FakeModel:
    def __init__(self, blocks):
        self.model = types.SimpleNamespace(layers=blocks)
        self.config = types.SimpleNamespace(max_position_embeddings=100)

    def to(self, device):
        return self

    def eval(self):
        return self

    def generate(self, **kwargs):
        return [[1, 2, 3, 4]]

    def __call__(self, **encoded):
        for block in self.model.layers:
            block()
        return None


class FakeTokenizer:
    chat_template = None

    def __call__(self, text, **kwargs):
        return {"input_ids": FakeTensor((1, 3))}

    def decode(self, values, **kwargs):
        return "steered output"


class SteeringTests(unittest.TestCase):
    def torch(self):
        return types.SimpleNamespace(
            inference_mode=contextlib.nullcontext,
            linalg=types.SimpleNamespace(
                vector_norm=lambda tensor, dim=None: FakeScalar(tensor.norm)
            ),
            median=lambda tensor: tensor,
        )

    def test_parse_and_bounds(self):
        self.assertEqual(
            parse_specs(["1:2:-0.5"]), [{"layer": 1, "neuron": 2, "dose": -0.5}]
        )
        for values in [
            [],
            ["x"],
            ["1:2:nan"],
            ["1:2:inf"],
            ["-1:0:1"],
            ["256:0:1"],
            ["0:1000001:1"],
            ["0:1:11"],
            ["0:1:1", "0:1:1"],
        ]:
            with self.subTest(values=values), self.assertRaises(NeuronSteerError):
                parse_specs(values)

    def test_prompt_and_generation_bounds(self):
        for prompt in ["", " ", "x" * 10001, None, "\ud800"]:
            with self.assertRaises(NeuronSteerError):
                validate_prompt(prompt)
        for values in [
            {"max_tokens": 0},
            {"temperature": float("nan")},
            {"seed": 100000001},
            {"context_size": -1},
        ]:
            defaults = dict(max_tokens=10, temperature=0, context_size=0, seed=0)
            defaults.update(values)
            with self.assertRaises(NeuronSteerError):
                validate_generation_options(**defaults)

    def test_math_and_hook_removal_contract(self):
        down = FakeDown()
        block = FakeBlock(down)
        model = FakeModel([block])
        report, handles = install_steering(
            model, {}, parse_specs(["0:3:1"]), self.torch()
        )
        self.assertEqual(report["neurons"][0]["scale"], 1.5)
        self.assertTrue(all(h.removed for h in block.hooks))
        value = down.hooks[0].function(down, (), FakeTensor((1, 3, 4)))
        self.assertEqual(value.added.value, 1.5)
        for handle in handles:
            handle.remove()
        self.assertTrue(all(h.removed for h in down.hooks))

    def test_gpt2_orientation(self):
        down = FakeConvDown()
        model = FakeModel([FakeBlock(down)])
        report, handles = install_steering(
            model, {}, parse_specs(["0:7:1"]), self.torch()
        )
        self.assertEqual(report["neurons"][0]["scale"], 1.5)
        for handle in handles:
            handle.remove()

    def test_bad_second_layer_cleans_calibration_hooks(self):
        block = FakeBlock(FakeDown())
        model = FakeModel([block])
        with self.assertRaises(NeuronSteerError):
            install_steering(model, {}, parse_specs(["0:1:1", "1:1:1"]), self.torch())
        self.assertTrue(all(h.removed for h in block.hooks))

    def test_bad_second_target_cleans_steering_hooks(self):
        down = FakeDown()
        model = FakeModel([FakeBlock(down)])
        with self.assertRaises(NeuronSteerError):
            install_steering(model, {}, parse_specs(["0:1:1", "0:99:1"]), self.torch())
        self.assertTrue(all(h.removed for h in down.hooks))

    def test_nonfinite_calibration_rejected(self):
        block = FakeBlock(FakeDown())
        block.output.norm = float("nan")
        with self.assertRaises(NeuronSteerError):
            install_steering(
                FakeModel([block]), {}, parse_specs(["0:1:1"]), self.torch()
            )
