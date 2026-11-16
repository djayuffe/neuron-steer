import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import test_jobs
from test_steering import FakeBlock, FakeDown, FakeModel, FakeScalar, FakeTensor

from neuron_steer import evaluation, jobs, provenance, runtime
from neuron_steer.cli import main
from neuron_steer.experiments import (
    conditions,
    match_answer,
    normalize_experiment,
    trials,
)
from neuron_steer.steering import NeuronSteerError, install_steering


class EvidenceTests(unittest.TestCase):
    def job(self, directory):
        job = test_jobs.JobsTests().job(directory)
        pathlib.Path(job["prompts"]).write_text(
            '[{"category":"math","prompt":"2+2?","answers":["4"]},{"category":"math","prompt":"3+3?","answers":["6"]}]'
        )
        return job

    def test_checkpoint_identity_tracks_policy_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            prompts = jobs.load_prompts(pathlib.Path(job["prompts"]))
            before = jobs.checkpoint_identity(job, prompts)
            with mock.patch.object(
                runtime, "SYSTEM_MESSAGE", "different system policy"
            ):
                self.assertNotEqual(before, jobs.checkpoint_identity(job, prompts))
            with mock.patch.object(
                provenance, "source_identity", return_value={"runtime.py": "different"}
            ):
                self.assertNotEqual(before, jobs.checkpoint_identity(job, prompts))

    def test_metadata_and_content_hash_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            asset = pathlib.Path(job["model"]) / "config.json"
            asset.write_text("{}")
            metadata = provenance.manifest(job)
            self.assertNotIn("sha256", metadata["model_assets"][0])
            job["asset_identity"] = "sha256"
            strict = provenance.manifest(job)
            self.assertEqual(len(strict["model_assets"][0]["sha256"]), 64)
            self.assertIn("system_message", strict)
            self.assertIn("max_tokens", strict["settings"])

    def test_missing_or_escaping_weight_shards_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            index = pathlib.Path(job["model"]) / "model.safetensors.index.json"
            for path in ("missing.safetensors", "../outside.safetensors"):
                index.write_text(json.dumps({"weight_map": {"weight": path}}))
                with self.assertRaises(NeuronSteerError):
                    provenance.manifest(job)

    def test_trial_design_and_control_doses(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            job.update(
                seeds="1,2",
                dose_scales="0.5,1",
                controls=["zero", "random", "reversed", "ablation"],
            )
            specs = jobs.validate_job(job)
            rows = trials(jobs.load_prompts(pathlib.Path(job["prompts"])), job)
            self.assertEqual(len(rows), 8)
            arms = conditions(specs, 0.5, job["controls"])
            self.assertEqual(arms["steered"][0][0]["dose"], 0.5)
            self.assertEqual(arms["zero"][0][0]["dose"], 0.0)
            self.assertEqual(arms["reversed"][0][0]["dose"], -0.5)
            self.assertEqual(arms["random"][1], "random")
            self.assertEqual(arms["without_0_1"][0], [])

    def test_invalid_experiment_settings(self):
        specs = [{"layer": 0, "neuron": 1, "dose": 10}]
        for setting in [
            {"seeds": "1,1"},
            {"seeds": "x"},
            {"dose_scales": "nan"},
            {"dose_scales": "2"},
            {"controls": ["unknown"]},
            {"order": "unknown"},
        ]:
            with self.subTest(setting=setting), self.assertRaises(NeuronSteerError):
                normalize_experiment({"seed": 0, **setting}, specs)

    def test_answer_keys_are_explicit_strict_matches(self):
        row = {"answers": ["4"], "match": "normalized"}
        self.assertTrue(match_answer(" 4\n", row))
        self.assertFalse(match_answer("4.", row))
        self.assertIsNone(match_answer("4", {}))
        self.assertFalse(match_answer("FOUR", {"answers": ["four"], "match": "exact"}))

    def test_repeated_trials_saved_and_resume_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            job.update(seeds="1,2", controls=["zero"], dose_scales="1")
            model = mock.Mock()
            model.info = {"device": "cpu"}
            model.generate.side_effect = lambda text, specs, **kwargs: {
                "content": "4" if text == "2+2?" else "6",
                "tokens": 1,
                "elapsed_seconds": 0.1,
                "possibly_truncated": False,
            }
            with mock.patch.object(jobs, "LocalRuntime", return_value=model):
                jobs.run_job(job)
            data = json.loads(pathlib.Path(job["output"]).read_text())
            self.assertEqual(data["version"], 3)
            self.assertEqual(data["summary"]["trials"], 4)
            self.assertEqual(data["summary"]["runs"], 12)
            self.assertEqual(data["summary"]["reference_accuracy"]["steered"], 1.0)
            self.assertIn("source_sha256", data["experiment_manifest"])
            self.assertEqual(
                data["records"][1]["execution_order"][:2], ["steered", "baseline"]
            )
            job["resume"] = True
            with mock.patch.object(jobs, "LocalRuntime") as loader:
                jobs.run_job(job)
            loader.assert_not_called()
            data["records"][0]["baseline"]["execution"]["seed"] = 999
            pathlib.Path(job["output"]).write_text(json.dumps(data))
            with self.assertRaisesRegex(NeuronSteerError, "execution settings"):
                jobs.run_job(job)

    def test_truncated_pairs_are_excluded_from_scored_primary_results(self):
        def record(index, before, after, truncated=False):
            return {
                "index": index,
                "prompt_index": index,
                "prompt": "synthetic",
                "baseline": {
                    "content": before,
                    "elapsed_seconds": 0.0,
                    "reference_match": False,
                },
                "steered": {
                    "content": after,
                    "elapsed_seconds": 0.0,
                    "reference_match": True,
                    "possibly_truncated": truncated,
                },
                "metrics": evaluation.metric_row(before, after),
            }

        summary = evaluation.summarize(
            [
                record(1, "wrong", "correct"),
                record(2, "wrong", "correct"),
                record(3, "wrong", "cut", True),
            ]
        )
        self.assertEqual(summary["complete_pairs"], 2)
        self.assertEqual(summary["possibly_truncated_pairs"], 1)
        self.assertEqual(summary["scored_complete_pairs"], 2)
        self.assertEqual(
            summary["reference_accuracy"]["cluster_bootstrap_95_interval"], [1.0, 1.0]
        )
        self.assertFalse(summary["truthfulness_verified"])

    def test_hook_installation_is_not_reported_as_execution(self):
        down = FakeDown()
        model = FakeModel([FakeBlock(down)])
        torch = mock.Mock()
        torch.inference_mode = contextlib.nullcontext
        # Use the small deterministic tensor double without optional methods.
        from types import SimpleNamespace

        torch = SimpleNamespace(
            inference_mode=contextlib.nullcontext,
            linalg=SimpleNamespace(
                vector_norm=lambda tensor, dim=None: FakeScalar(tensor.norm)
            ),
            median=lambda tensor: tensor,
        )
        report, handles = install_steering(
            model,
            {"input_ids": FakeTensor((1, 3))},
            [{"layer": 0, "neuron": 1, "dose": 1.0}],
            torch,
        )
        self.assertTrue(report["installed"])
        self.assertFalse(report["applied"])
        try:
            down.hooks[-1].function(down, (), FakeTensor((1, 3, 4)))
            self.assertTrue(report["applied"])
            self.assertEqual(report["neurons"][0]["calls"], 1)
            self.assertEqual(report["neurons"][0]["positions_touched"], 3)
        finally:
            for handle in handles:
                handle.remove()

    def test_dry_run_reports_bounded_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(pathlib.Path(directory) / "out.json")
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                code = main(
                    [
                        "evaluate",
                        "--model",
                        "/not-loaded",
                        "--neuron",
                        "0:0:0.1",
                        "--seeds",
                        "1,2",
                        "--dose-scales",
                        "0.5,1",
                        "--control",
                        "zero",
                        "--limit",
                        "1",
                        "--output",
                        output,
                        "--dry-run",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stream.getvalue())["preflight"]["trials"], 4)
            self.assertEqual(
                json.loads(stream.getvalue())["preflight"]["generations"], 12
            )
