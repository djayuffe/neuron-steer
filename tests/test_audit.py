import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import test_jobs

from neuron_steer import evaluation, jobs
from neuron_steer.cli import main
from neuron_steer.steering import NeuronSteerError, validate_generation_options
from neuron_steer.storage import strict_json, write_json


class AuditTests(unittest.TestCase):
    def setup_job(self, directory):
        job = test_jobs.JobsTests().job(directory)
        pathlib.Path(job["prompts"]).write_text(
            '[{"category":"test","prompt":"synthetic"}]'
        )
        return job

    def evaluate(self, job):
        runtime = mock.Mock()
        runtime.info = {"device": "cpu"}
        runtime.generate.return_value = {
            "content": "text",
            "tokens": 1,
            "elapsed_seconds": 0.1,
        }
        with mock.patch.object(jobs, "LocalRuntime", return_value=runtime):
            jobs.run_job(job)

    def test_reports_cannot_touch_model_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            config = pathlib.Path(job["model"]) / "config.json"
            config.write_text('{"original":true}')
            job["markdown_out"] = str(config)
            with self.assertRaises(NeuronSteerError):
                jobs.run_job(job)
            self.assertEqual(config.read_text(), '{"original":true}')

    def test_existing_markdown_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            path = pathlib.Path(job["markdown_out"])
            path.write_text("user document")
            with self.assertRaises(NeuronSteerError):
                jobs.run_job(job)
            self.assertEqual(path.read_text(), "user document")

    def test_report_symlink_and_hardlink_to_model_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            config = pathlib.Path(job["model"]) / "config.json"
            config.write_text("{}")
            path = pathlib.Path(job["markdown_out"])
            path.symlink_to(config)
            with self.assertRaises(NeuronSteerError):
                jobs.run_job(job)
            path.unlink()
            os.link(config, path)
            with self.assertRaises(NeuronSteerError):
                jobs.run_job(job)

    def test_prompt_hardlink_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            os.link(job["prompts"], job["markdown_out"])
            with self.assertRaises(NeuronSteerError):
                jobs.run_job(job)

    def test_old_fixed_temp_filename_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            old = pathlib.Path(job["output"] + ".tmp")
            old.write_text("user file")
            self.evaluate(job)
            self.assertEqual(old.read_text(), "user file")

    def test_overlapping_checkpoint_use_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "out.json"
            with (
                jobs.checkpoint_lock(output),
                self.assertRaisesRegex(NeuronSteerError, "already in use"),
            ):
                with jobs.checkpoint_lock(output):
                    pass

    def test_shared_markdown_output_is_also_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            with (
                jobs.checkpoint_lock(pathlib.Path(job["markdown_out"])),
                self.assertRaisesRegex(NeuronSteerError, "already in use"),
            ):
                jobs.run_job(job)

    def test_truncated_checkpoint_records_and_false_indices_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            self.evaluate(job)
            job["resume"] = True
            path = pathlib.Path(job["output"])
            original = json.loads(path.read_text())
            for mutation in [
                "content",
                "tokens",
                "elapsed_seconds",
                "metrics",
                "index",
            ]:
                payload = json.loads(json.dumps(original))
                row = payload["records"][0]
                if mutation == "metrics":
                    row["metrics"] = {}
                elif mutation == "index":
                    row["index"] = True
                else:
                    row["baseline"].pop(mutation)
                path.write_text(json.dumps(payload))
                with (
                    self.subTest(mutation=mutation),
                    mock.patch.object(jobs, "LocalRuntime") as loader,
                    self.assertRaises(NeuronSteerError),
                ):
                    jobs.run_job(job)
                loader.assert_not_called()

    def test_v1_checkpoint_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            self.evaluate(job)
            job["resume"] = True
            path = pathlib.Path(job["output"])
            payload = json.loads(path.read_text())
            payload["version"] = 1
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(NeuronSteerError, "unsupported checkpoint"):
                jobs.run_job(job)

    def test_runtime_version_changes_invalidate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            prompts = jobs.load_prompts(pathlib.Path(job["prompts"]))
            before = jobs.checkpoint_identity(job, prompts)
            with mock.patch.object(
                __import__(
                    "neuron_steer.provenance", fromlist=["importlib"]
                ).importlib.metadata,
                "version",
                return_value="changed-version",
            ):
                self.assertNotEqual(before, jobs.checkpoint_identity(job, prompts))

    def test_strict_json_duplicates_nan_and_depth(self):
        for source in ['{"a":1,"a":2}', '{"a":NaN}', "[" * 40 + "0" + "]" * 40]:
            with self.subTest(source=source[:20]), self.assertRaises(NeuronSteerError):
                strict_json(source)

    def test_atomic_json_rejects_nonfinite_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "out.json"
            path.write_text("original")
            with self.assertRaises(ValueError):
                write_json(path, {"invalid": float("nan")})
            self.assertEqual(path.read_text(), "original")

    def test_prompt_whitespace_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "p.json"
            path.write_text(
                json.dumps([{"category": "test", "prompt": "  indented\n"}])
            )
            self.assertEqual(evaluation.load_prompts(path)[0]["prompt"], "  indented\n")

    def test_markdown_escaping_and_actual_prompt_count(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.setup_job(directory)
            self.evaluate(job)
            payload = json.loads(pathlib.Path(job["output"]).read_text())
            payload["records"][0]["baseline"]["content"] = (
                "~~~~\n<script>x</script>\n~~~~"
            )
            payload["records"][0]["prompt"] = "[link](https://example.com)|\n# title"
            text = evaluation.markdown(payload)
            self.assertIn("# 1-prompt", text)
            self.assertIn("| Main-arm runs | 1 | 1 |", text)
            self.assertIn("~~~~~text\n~~~~", text)
            self.assertIn(r"\[link\]", text)

    def test_cli_malformed_worker_error_never_raises_traceback(self):
        result = mock.Mock(returncode=2, stdout="[]", stderr="")
        with (
            mock.patch("neuron_steer.cli.bounded_run", return_value=result),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            self.assertEqual(
                main(
                    [
                        "generate",
                        "--model",
                        "/tmp/unused",
                        "--neuron",
                        "0:0:0",
                        "--prompt",
                        "synthetic",
                    ]
                ),
                2,
            )
        self.assertIn("invalid worker diagnostic", err.getvalue())

    def test_boolean_and_float_integer_parameters_rejected(self):
        for key, value in [
            ("max_tokens", True),
            ("max_tokens", 1.5),
            ("context_size", False),
            ("seed", True),
            ("temperature", "0"),
        ]:
            params = {"max_tokens": 4, "temperature": 0, "context_size": 0, "seed": 0}
            params[key] = value
            with self.subTest(key=key), self.assertRaises(NeuronSteerError):
                validate_generation_options(**params)
