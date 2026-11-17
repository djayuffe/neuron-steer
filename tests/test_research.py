import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import test_transformers_integration as integration

from neuron_steer import research
from neuron_steer.cli import main
from neuron_steer.jobs import run_job
from neuron_steer.steering import NeuronSteerError


class ResearchTests(unittest.TestCase):
    def job(self, directory):
        root = pathlib.Path(directory)
        (root / "config.json").write_text(
            json.dumps(
                {
                    "model_type": "synthetic",
                    "text_config": {"hidden_size": 8, "num_hidden_layers": 2},
                }
            )
        )
        (root / "tokenizer_config.json").write_text(
            json.dumps({"chat_template": [{"name": "default", "template": "hello"}]})
        )
        prompts = root / "prompts.json"
        prompts.write_text(
            json.dumps(
                [
                    {"category": "positive", "prompt": "hello"},
                    {"category": "negative", "prompt": "world"},
                ]
            )
        )
        return {
            "operation": "probe",
            "model": directory,
            "prompts": str(prompts),
            "layers": [0],
            "top_k": 4,
            "limit": 64,
            "max_input_tokens": 128,
            "positions": "last",
            "timeout": 30,
            "device": "cpu",
            "positive": "positive",
            "negative": "negative",
            "references": [],
        }

    def test_static_inspection_does_not_load_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            self.job(directory)
            with mock.patch.object(research, "LocalRuntime") as runtime:
                result = run_job({"operation": "inspect", "model": directory})
            runtime.assert_not_called()
            self.assertFalse(result["model_loaded"])
            self.assertFalse(result["projection_support_verified"])
            self.assertEqual(result["config_claims"]["hidden_size"], 8)
            self.assertEqual(result["tokenizer"]["template_names"], ["default"])

    def test_static_inspection_finds_external_chat_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            self.job(directory)
            root = pathlib.Path(directory)
            (root / "chat_template.jinja").write_text("hello")
            (root / "chat_templates").mkdir()
            (root / "chat_templates" / "alternate.jinja").write_text("world")
            result = run_job({"operation": "inspect", "model": directory})
            self.assertEqual(
                result["tokenizer"]["template_names"], ["alternate", "default"]
            )

    def test_invalid_research_options_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = self.job(directory)
            invalid = [
                {"layers": []},
                {"layers": [True]},
                {"layers": [0, 0]},
                {"layers": [256]},
                {"layers": list(range(9))},
                {"top_k": 65},
                {"limit": 0},
                {"max_input_tokens": 4097},
                {"positive": "positive", "negative": None},
                {"positive": "missing"},
                {"positions": "middle"},
                {"asset_identity": "unknown"},
                {"chat_template": []},
                {"timeout": float("nan")},
                {"system_role": "unknown"},
            ]
            for change in invalid:
                with self.subTest(change=change), self.assertRaises(NeuronSteerError):
                    research.validate_research_job({**base, **change})

    def test_probe_dry_run_preflights_categories_without_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            with (
                mock.patch.object(research, "LocalRuntime") as runtime,
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                status = main(
                    [
                        "probe",
                        "--model",
                        directory,
                        "--prompts",
                        job["prompts"],
                        "--layer",
                        "0",
                        "--positive",
                        "positive",
                        "--negative",
                        "negative",
                        "--dry-run",
                    ]
                )
            runtime.assert_not_called()
            self.assertEqual(status, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["prompts_selected"], 2)
            self.assertFalse(result["model_loaded"])

    def test_limit_cannot_silently_remove_contrast_group(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            job["limit"] = 1
            with self.assertRaisesRegex(NeuronSteerError, "both contrast"):
                research.validate_research_job(job)

    def test_static_inspection_rejects_malformed_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self.job(directory)
            (pathlib.Path(directory) / "config.json").write_text("[]")
            with self.assertRaisesRegex(NeuronSteerError, "object"):
                run_job({"operation": "inspect", "model": directory})


@unittest.skipIf(
    integration.torch is None, "optional real Transformers runtime unavailable"
)
class RealProbeTests(unittest.TestCase):
    def setup_runtime(self, directory):
        model = pathlib.Path(directory) / "model"
        runtime = integration.TransformersIntegrationTests().runtime(str(model))
        job = {
            "operation": "probe",
            "model": str(model),
            "device": "cpu",
            "layers": [0],
            "top_k": 5,
            "limit": 64,
            "max_input_tokens": 128,
            "positions": "last",
            "positive": "positive",
            "negative": "negative",
        }
        rows = [
            {"index": 1, "category": "positive", "prompt": "hello"},
            {"index": 2, "category": "negative", "prompt": "hello world"},
        ]
        return runtime, job, rows

    def test_linear_layout_and_nonfloating_weights(self):
        torch = integration.torch
        module = torch.nn.Linear(32, 8)
        info = research.projection_info(module)
        self.assertEqual(info["orientation"], "output_input")
        self.assertEqual(info["mlp_width"], 32)
        module.weight = torch.nn.Parameter(
            torch.zeros((8, 32), dtype=torch.int8), requires_grad=False
        )
        with self.assertRaisesRegex(NeuronSteerError, "floating"):
            research.projection_info(module)

    def test_real_inspection_and_projection_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            with mock.patch.object(research, "LocalRuntime", return_value=runtime):
                result = run_job(
                    {"operation": "inspect", "model": job["model"], "load": True}
                )
            self.assertTrue(result["projection_support_verified"])
            self.assertEqual(result["layers"][0]["orientation"], "input_output")
            self.assertEqual(result["layers"][0]["mlp_width"], 32)
            self.assertEqual(
                result["layers"][0]["projection_module_path"],
                "transformer.h.0.mlp.c_proj",
            )

    def test_contrast_scores_match_real_forward_inputs(self):
        torch = integration.torch
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            result = research.probe_runtime(runtime, job, rows)
            module = runtime.model.transformer.h[0].mlp.c_proj
            captured = []
            handle = module.register_forward_hook(
                lambda mod, inputs, output: captured.append(
                    inputs[0].detach()[0, -1].float().cpu()
                )
            )
            try:
                for row in result["records"]:
                    with torch.inference_mode():
                        runtime.model(
                            **{
                                key: torch.tensor(
                                    entry["values"],
                                    dtype=getattr(
                                        torch, entry["dtype"].removeprefix("torch.")
                                    ),
                                )
                                for key, entry in row["model_inputs"].items()
                            },
                            use_cache=False,
                        )
            finally:
                handle.remove()
            delta = captured[0] - captured[1]
            norms = torch.linalg.vector_norm(module.weight.detach().float(), dim=1)
            scores = delta.abs() * norms
            expected = torch.argsort(scores, descending=True, stable=True)[:5].tolist()
            actual = result["layers"][0]["candidates"]
            self.assertEqual([c["neuron"] for c in actual], expected)
            for candidate in actual:
                n = candidate["neuron"]
                self.assertAlmostEqual(candidate["score"], float(scores[n]), places=6)
                self.assertAlmostEqual(
                    candidate["contrast_activation_delta"], float(delta[n]), places=6
                )
            self.assertEqual(len(module._forward_hooks), 0)
            self.assertFalse(result["causality_verified"])

    def test_probe_position_policies_and_single_token_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            job.update(positive=None, negative=None)
            for policy in ("last", "all", "skip-first"):
                with self.subTest(policy=policy):
                    job["positions"] = policy
                    result = research.probe_runtime(runtime, job, rows)
                    for record in result["records"]:
                        expected = (
                            1
                            if policy == "last"
                            else record["input_tokens"]
                            - (
                                1
                                if policy == "skip-first" and record["input_tokens"] > 1
                                else 0
                            )
                        )
                        self.assertEqual(record["positions_used"]["0"], expected)
                    self.assertEqual(len(result["layers"][0]["candidates"]), 5)

    def test_context_and_invalid_layer_rejected_before_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            job["max_input_tokens"] = 1
            with self.assertRaisesRegex(NeuronSteerError, "token/context"):
                research.probe_runtime(runtime, job, rows)
            job["layers"] = [1]
            with self.assertRaisesRegex(NeuronSteerError, "outside"):
                research.probe_runtime(runtime, job, rows)
            self.assertEqual(
                len(runtime.model.transformer.h[0].mlp.c_proj._forward_hooks), 0
            )

    def test_empty_rendered_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            runtime.tokenizer.chat_template = "{{ '' }}"
            with self.assertRaisesRegex(NeuronSteerError, "at least one"):
                research.probe_runtime(runtime, job, rows)
            self.assertEqual(
                len(runtime.model.transformer.h[0].mlp.c_proj._forward_hooks), 0
            )

    def test_failed_or_bypassed_forward_always_removes_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            for behavior in (
                {"side_effect": RuntimeError("synthetic forward failure")},
                {"return_value": {}},
            ):
                with (
                    self.subTest(behavior=behavior),
                    mock.patch.object(runtime.model, "forward", **behavior),
                ):
                    with self.assertRaises((RuntimeError, NeuronSteerError)):
                        research.probe_runtime(runtime, job, rows)
                self.assertEqual(
                    len(runtime.model.transformer.h[0].mlp.c_proj._forward_hooks), 0
                )

    def test_probe_job_has_recoverable_identity_and_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, job, rows = self.setup_runtime(directory)
            prompts = pathlib.Path(directory) / "prompts.json"
            prompts.write_text(json.dumps(rows))
            job["prompts"] = str(prompts)
            job["references"] = [
                "https://www.neuronpedia.org/gemma-2-2b/20-gemmascope-res-16k/10956"
            ]
            with mock.patch.object(research, "LocalRuntime", return_value=runtime):
                result = run_job(job)
            self.assertEqual(
                result["experiment_manifest"]["probe_settings"]["layers"], [0]
            )
            self.assertIn("research.py", result["experiment_manifest"]["source_sha256"])
            self.assertTrue(result["references_are_annotations"])
            self.assertFalse(result["hosted_text_submitted"])
            json.dumps(result, allow_nan=False)
