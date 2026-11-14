import contextlib
import types
import unittest
from unittest import mock
from test_steering import FakeModel, FakeBlock, FakeDown, FakeTokenizer, FakeScalar
from neuron_steer.runtime import LocalRuntime, build_chat_prompt
from neuron_steer.steering import ModelContextOverflow


class RuntimeTests(unittest.TestCase):
    def runtime(self):
        runtime = object.__new__(LocalRuntime)
        runtime.model = FakeModel([FakeBlock(FakeDown())])
        runtime.model.generation_config = types.SimpleNamespace(eos_token_id=0)
        runtime.tokenizer = FakeTokenizer()
        runtime.device = "cpu"
        runtime.torch = types.SimpleNamespace(
            inference_mode=contextlib.nullcontext,
            manual_seed=mock.Mock(),
            linalg=types.SimpleNamespace(
                vector_norm=lambda tensor, dim=None: FakeScalar(tensor.norm)
            ),
            median=lambda tensor: tensor,
        )
        return runtime

    def test_generate_and_cleanup(self):
        runtime = self.runtime()
        output = runtime.generate(
            "synthetic", [dict(layer=0, neuron=1, dose=1)], max_tokens=4, seed=5
        )
        self.assertEqual(output["content"], "steered output")
        self.assertEqual(output["tokens"], 1)
        self.assertFalse(output["reached_token_budget"])
        self.assertTrue(
            all(h.removed for h in runtime.model.model.layers[0].mlp.down_proj.hooks)
        )
        runtime.torch.manual_seed.assert_called_once_with(5)

    def test_generate_failure_cleans_hooks(self):
        runtime = self.runtime()
        runtime.model.generate = mock.Mock(side_effect=RuntimeError("test failure"))
        with self.assertRaises(RuntimeError):
            runtime.generate(
                "synthetic", [dict(layer=0, neuron=1, dose=1)], max_tokens=4
            )
        self.assertTrue(
            all(h.removed for h in runtime.model.model.layers[0].mlp.down_proj.hooks)
        )

    def test_context_bound_before_hook(self):
        runtime = self.runtime()
        with self.assertRaises(ModelContextOverflow):
            runtime.generate("synthetic", [], max_tokens=4, context_size=5)
        self.assertEqual(runtime.model.model.layers[0].hooks, [])

    def test_baseline_does_not_install_hooks(self):
        runtime = self.runtime()
        runtime.generate("synthetic", [], max_tokens=4)
        self.assertEqual(runtime.model.model.layers[0].hooks, [])
        self.assertEqual(runtime.model.model.layers[0].mlp.down_proj.hooks, [])

    def test_thinking_control(self):
        tokenizer = types.SimpleNamespace(
            chat_template="{{ enable_thinking }}",
            apply_chat_template=mock.Mock(return_value="formatted"),
        )
        self.assertEqual(build_chat_prompt(tokenizer, "text"), "formatted")
        self.assertFalse(
            tokenizer.apply_chat_template.call_args.kwargs["enable_thinking"]
        )
