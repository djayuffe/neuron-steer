"""Real tokenizer + tiny random causal model; no pretrained weights or network."""

import tempfile
import unittest
from unittest import mock

try:
    import jinja2
    import torch
    import transformers
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from tokenizers.processors import TemplateProcessing
except ImportError:
    torch = None
from neuron_steer.runtime import LocalRuntime, prepare_chat_prompt
from neuron_steer.steering import NeuronSteerError


@unittest.skipIf(torch is None, "optional real Transformers runtime unavailable")
class TransformersIntegrationTests(unittest.TestCase):
    def tokenizer(self):
        backend = Tokenizer(
            WordLevel(
                {
                    "<unk>": 0,
                    "<bos>": 1,
                    "<eos>": 2,
                    "hello": 3,
                    "world": 4,
                    "True": 5,
                    "False": 6,
                    "answer": 7,
                },
                unk_token="<unk>",
            )
        )
        backend.pre_tokenizer = Whitespace()
        backend.post_processor = TemplateProcessing(
            single="<bos> $A", special_tokens=[("<bos>", 1)]
        )
        tokenizer = transformers.PreTrainedTokenizerFast(
            tokenizer_object=backend,
            unk_token="<unk>",
            bos_token="<bos>",
            eos_token="<eos>",
            pad_token="<eos>",
        )
        tokenizer.chat_template = "{{ bos_token }}{% for message in messages %}{{ message['content'] }} {% endfor %}"
        return tokenizer

    def runtime(self, directory):
        torch.manual_seed(0)
        model = transformers.GPT2LMHeadModel(
            transformers.GPT2Config(
                vocab_size=8,
                n_positions=256,
                n_ctx=256,
                n_embd=8,
                n_layer=1,
                n_head=2,
                bos_token_id=1,
                eos_token_id=2,
                pad_token_id=2,
            )
        )
        model.generation_config.min_new_tokens = 3
        model.generation_config.forced_eos_token_id = 2
        model.generation_config.num_beams = 2
        model.generation_config.num_return_sequences = 2
        model.save_pretrained(directory, safe_serialization=True)
        self.tokenizer().save_pretrained(directory)
        return LocalRuntime(directory, "cpu")

    def test_exactly_one_bos_and_explicit_single_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            output = runtime.generate("hello", [], max_tokens=3)
            self.assertEqual(output["manifest"]["input_token_ids"].count(1), 1)
            self.assertIn("attention_mask", output["manifest"]["model_inputs"])
            self.assertEqual(
                output["manifest"]["model_inputs"]["input_ids"]["values"][0],
                output["manifest"]["input_token_ids"],
            )
            self.assertFalse(output["manifest"]["formatting"]["add_special_tokens"])
            self.assertEqual(
                output["manifest"]["generation_config"]["num_return_sequences"], 1
            )
            self.assertEqual(output["manifest"]["generation_config"]["num_beams"], 1)
            self.assertTrue(output["ended_with_eos"])
            self.assertTrue(output["reached_token_budget"])
            self.assertTrue(output["possibly_truncated"])

    def test_real_projection_executes_and_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            result = runtime.generate(
                "hello", [{"layer": 0, "neuron": 12, "dose": 0.1}], max_tokens=3
            )
            steering = result["steering"]
            self.assertTrue(steering["installed"])
            self.assertTrue(steering["applied"])
            self.assertGreater(steering["neurons"][0]["calls"], 0)
            self.assertGreater(steering["neurons"][0]["positions_touched"], 0)
            self.assertAlmostEqual(
                steering["neurons"][0]["requested_injection_norm"],
                steering["neurons"][0]["achieved_injection_norm"],
                places=5,
            )
            self.assertEqual(
                len(runtime.model.transformer.h[0].mlp.c_proj._forward_hooks), 0
            )

    def test_role_rejection_falls_back_but_syntax_errors_do_not(self):
        tokenizer = self.tokenizer()
        tokenizer.chat_template = "{% if messages[0]['role'] == 'system' %}{{ raise_exception('Only user and assistant roles are supported!') }}{% endif %}{{ messages[0]['content'] }}"
        text, metadata = prepare_chat_prompt(tokenizer, "hello")
        self.assertIn("hello", text)
        self.assertEqual(metadata["system_role_effective"], "merged_user")
        with self.assertRaises(jinja2.TemplateError):
            prepare_chat_prompt(tokenizer, "hello", system_role="strict")
        tokenizer.chat_template = "{% invalid syntax %}"
        with self.assertRaises(jinja2.TemplateSyntaxError):
            prepare_chat_prompt(tokenizer, "hello")

    def test_dictionary_templates_resolve_thinking_flag(self):
        tokenizer = self.tokenizer()
        tokenizer.chat_template = {
            "default": "{{ enable_thinking | default(true) }}",
            "other": "world",
        }
        text, metadata = prepare_chat_prompt(tokenizer, "hello", True)
        self.assertEqual(text, "False")
        self.assertTrue(metadata["thinking_control_passed"])
        self.assertEqual(prepare_chat_prompt(tokenizer, "hello", False)[0], "True")
        self.assertEqual(
            prepare_chat_prompt(tokenizer, "hello", chat_template="other")[0], "world"
        )
        with self.assertRaises(NeuronSteerError):
            prepare_chat_prompt(tokenizer, "hello", chat_template="missing")

    def test_random_control_has_matched_norm(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            result = runtime.generate(
                "hello",
                [{"layer": 0, "neuron": 12, "dose": 0.1}],
                max_tokens=3,
                control="random",
                control_seed=7,
            )
            target = result["steering"]["neurons"][0]
            self.assertAlmostEqual(
                target["requested_injection_norm"],
                target["achieved_injection_norm"],
                places=5,
            )
            self.assertTrue(result["steering"]["applied"])

    def test_completion_budget_rejects_inherited_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            with self.assertRaisesRegex(NeuronSteerError, "min_new_tokens"):
                runtime.generate("hello", [], max_tokens=1)

    def test_installed_but_unused_hook_fails_and_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            # Calibration uses forward; generation deliberately bypasses the model.
            with mock.patch.object(
                runtime.model, "generate", return_value=torch.tensor([[1, 2]])
            ):
                with self.assertRaisesRegex(NeuronSteerError, "never executed"):
                    runtime.generate(
                        "hello", [{"layer": 0, "neuron": 12, "dose": 0.1}], max_tokens=3
                    )
            self.assertEqual(
                len(runtime.model.transformer.h[0].mlp.c_proj._forward_hooks), 0
            )
